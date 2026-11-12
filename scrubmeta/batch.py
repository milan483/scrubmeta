"""Deterministic directory processing for scrubmeta's single-file API."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from . import MODES, clean, scan, supported_extensions, verify
from .common import Field, ScrubError

_COMMANDS = {"scan", "clean", "verify"}
_SUMMARY_KEYS = (
    "discovered",
    "processed",
    "cleaned",
    "verified",
    "skipped_unsupported",
    "skipped_nonregular",
    "remaining",
    "unsupported",
    "errors",
)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _resolved(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise ScrubError(f"Cannot resolve batch path {path}: {exc}") from exc


def _overlaps(left: Path, right: Path) -> bool:
    left_resolved = os.path.normcase(os.fspath(_resolved(left)))
    right_resolved = os.path.normcase(os.fspath(_resolved(right)))
    left_path, right_path = Path(left_resolved), Path(right_resolved)
    try:
        left_path.relative_to(right_path)
        return True
    except ValueError:
        pass
    try:
        right_path.relative_to(left_path)
        return True
    except ValueError:
        return False


def _reject_symlink_components(path: Path) -> None:
    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            if _is_reparse(current.lstat()):
                raise ScrubError(f"Output path contains a symlink component: {current}")
        except FileNotFoundError:
            # Check the remaining components when created for each output.
            continue
        except OSError as exc:
            raise ScrubError(
                f"Cannot inspect output path component {current}: {exc}"
            ) from exc


def _is_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse and getattr(info, "st_file_attributes", 0) & reparse)


def _walk(root: Path, recursive: bool) -> list[tuple[Path, str]]:
    """Return file candidates and skip markers without following symlinks."""
    candidates: list[tuple[Path, str]] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                snapshot = sorted(
                    entries, key=lambda entry: (entry.name.casefold(), entry.name)
                )
        except OSError as exc:
            if directory == root:
                raise ScrubError(
                    f"Cannot enumerate directory {directory}: {exc}"
                ) from exc
            candidates.append((directory, f"error:Cannot enumerate directory: {exc}"))
            continue

        child_directories: list[Path] = []
        for entry in snapshot:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
                if _is_reparse(info):
                    candidates.append((path, "skipped_nonregular"))
                elif stat.S_ISDIR(info.st_mode):
                    if recursive:
                        child_directories.append(path)
                elif not stat.S_ISREG(info.st_mode):
                    candidates.append((path, "skipped_nonregular"))
                elif path.suffix.casefold() not in supported_extensions():
                    candidates.append((path, "skipped_unsupported"))
                else:
                    candidates.append((path, "file"))
            except OSError as exc:
                candidates.append((path, "error"))
                # Keep enumeration going; a disappearing or inaccessible entry
                # is reported as a per-file error below.
                candidates[-1] = (path, f"error:{exc}")

        # Push reverse lexical order so stack traversal remains lexical.
        pending.extend(reversed(child_directories))

    return sorted(
        candidates,
        key=lambda item: (
            item[0].relative_to(root).as_posix().casefold(),
            item[0].relative_to(root).as_posix(),
        ),
    )


def _field_report(fields: list[Field], show_values: bool) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for field in fields:
        item = {"name": field.name, "disposition": field.disposition}
        if show_values and field.value is not None:
            item["value"] = field.value
        output.append(item)
    return output


def _has(fields: list[Field], disposition: str) -> bool:
    return any(field.disposition == disposition for field in fields)


def _summary() -> dict[str, int]:
    return dict.fromkeys(_SUMMARY_KEYS, 0)


def _set_file_counts(summary: dict[str, int], fields: list[Field]) -> None:
    if _has(fields, "removable"):
        summary["remaining"] += 1
    if _has(fields, "unsupported"):
        summary["unsupported"] += 1


def _verify_code(summary: dict[str, int]) -> int:
    if summary["errors"]:
        return 2
    if summary["remaining"]:
        return 1
    if summary["unsupported"]:
        return 3
    return 0


def run_batch(
    path: Path,
    command: str,
    mode: str = "privacy",
    recursive: bool = False,
    output_dir: Path | None = None,
    show_values: bool = False,
) -> tuple[dict, int]:
    """Run scan, clean, or verify over regular files in a directory.

    ``summary`` counters count files, except ``discovered`` which also includes
    unsupported entries and nonregular entries such as symlinks. Ordinary
    directories are traversal containers and are not counted as files.
    """
    source = Path(path)
    if command not in _COMMANDS:
        raise ScrubError(
            f"Unknown batch command {command!r}; choose scan, clean, or verify."
        )
    if mode not in MODES:
        raise ScrubError(f"Unknown mode {mode!r}. Choose ai, privacy, or max.")
    try:
        source_info = source.lstat()
    except OSError as exc:
        raise ScrubError(
            f"Cannot inspect batch source directory {source}: {exc}"
        ) from exc
    if _is_reparse(source_info):
        raise ScrubError("Batch source directory must not be a symlink.")
    if not stat.S_ISDIR(source_info.st_mode):
        raise ScrubError("Batch source must be an existing directory.")
    source = _absolute(source)

    output_root: Path | None = None
    if command == "clean":
        output_root = (
            Path(output_dir)
            if output_dir is not None
            else source.with_name(f"{source.name}_clean")
        )
        output_root = _absolute(output_root)
        _reject_symlink_components(output_root)
        if _overlaps(source, output_root):
            raise ScrubError(
                "Output directory must not be inside, equal to, or an ancestor of the source directory."
            )
        if output_root.exists() and not output_root.is_dir():
            raise ScrubError(f"Output directory is not a directory: {output_root}")
    elif output_dir is not None:
        raise ScrubError("--output-dir is only valid for batch clean.")

    candidates = _walk(source, recursive)
    summary = _summary()
    results: list[dict] = []
    for file_path, kind in candidates:
        relative = file_path.relative_to(source).as_posix()
        summary["discovered"] += 1
        if kind == "skipped_unsupported":
            summary["skipped_unsupported"] += 1
            results.append({"path": relative, "status": kind, "fields": []})
            continue
        if kind == "skipped_nonregular":
            summary["skipped_nonregular"] += 1
            results.append({"path": relative, "status": kind, "fields": []})
            continue
        if kind.startswith("error:"):
            summary["processed"] += 1
            summary["errors"] += 1
            results.append(
                {
                    "path": relative,
                    "status": "error",
                    "error": kind[6:],
                    "fields": [],
                }
            )
            continue

        summary["processed"] += 1
        try:
            file_info = file_path.lstat()
            if _is_reparse(file_info) or not stat.S_ISREG(file_info.st_mode):
                raise ScrubError(
                    "Input changed after enumeration and is no longer a regular file."
                )
            if command == "scan":
                fields = scan(file_path, mode)
                _set_file_counts(summary, fields)
                results.append(
                    {
                        "path": relative,
                        "status": "scanned",
                        "fields": _field_report(fields, show_values),
                    }
                )
            elif command == "verify":
                verification = verify(file_path, mode)
                fields = verification.fields
                summary["verified"] += 1
                _set_file_counts(summary, fields)
                results.append(
                    {
                        "path": relative,
                        "status": verification.status,
                        "fields": _field_report(fields, show_values),
                    }
                )
            else:
                assert output_root is not None
                destination = output_root / relative
                _reject_symlink_components(destination.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                _reject_symlink_components(destination.parent)
                published, verification = clean(
                    file_path, mode, destination=destination
                )
                summary["cleaned"] += 1
                _set_file_counts(summary, verification.fields)
                results.append(
                    {
                        "path": relative,
                        "status": "cleaned",
                        "output": published.relative_to(output_root).as_posix(),
                        "fields": _field_report(verification.fields, show_values),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - isolate every file failure in a batch.
            summary["errors"] += 1
            results.append(
                {
                    "path": relative,
                    "status": "error",
                    "error": str(exc),
                    "fields": [],
                }
            )

    if command == "verify":
        code = _verify_code(summary)
        status = {
            0: "clean",
            1: "remaining",
            2: "error",
            3: "unsupported/unknown",
        }[code]
    elif summary["errors"]:
        code, status = 2, "error"
    elif command == "scan":
        code, status = 0, "scanned"
    else:
        code, status = 0, "cleaned"

    report = {
        "command": command,
        "mode": mode,
        "status": status,
        "results": results,
        "summary": summary,
    }
    return report, code
