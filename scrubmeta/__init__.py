"""Scan, clean, and verify selected embedded metadata."""

import os
import shutil
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

from .common import Field, ScrubError

MODES = ("ai", "privacy", "max")
_HANDLERS = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".jpg": "images",
    ".jpeg": "images",
    ".png": "images",
    ".webp": "images",
}


@dataclass(frozen=True)
class Verification:
    status: str
    readable: bool
    fields: list[Field]


def scan(path: str | Path, mode: str = "privacy") -> list[Field]:
    """Inspect a supported file without modifying it."""
    source = Path(path)
    return _handler(source, mode).scan(source, mode)


def verify(path: str | Path, mode: str = "privacy") -> Verification:
    """Check readability and remaining targeted or unsupported fields."""
    fields = scan(path, mode)
    if any(field.disposition == "removable" for field in fields):
        status = "remaining"
    elif any(field.disposition == "unsupported" for field in fields):
        status = "unsupported/unknown"
    else:
        status = "clean"
    return Verification(status, True, fields)


def clean(path: str | Path, mode: str = "privacy") -> tuple[Path, Verification]:
    """Publish a validated sibling copy without replacing an existing path."""
    source = Path(path)
    handler = _handler(source, mode)
    destination = source.with_name(f"{source.stem}_clean{source.suffix}")
    if os.path.lexists(destination):
        raise ScrubError("Output already exists. Move it aside before cleaning again.")

    with TemporaryDirectory(prefix=".scrubmeta-", dir=source.parent) as directory:
        # Inspect and clean the same snapshot, even if the source path is replaced.
        snapshot = Path(directory) / f"input{source.suffix}"
        output = Path(directory) / f"output{source.suffix}"
        shutil.copyfile(source, snapshot)
        handler.clean(snapshot, output, mode)
        result = verify(output, mode)
        if result.status == "remaining":
            raise ScrubError(
                "Cleaning left targeted metadata. No output was published."
            )
        if os.name == "posix":
            output.chmod(0o600)
        try:
            # A same-filesystem hard link publishes the whole file without a clobber race.
            os.link(output, destination)
        except FileExistsError as exc:
            raise ScrubError(
                "Output already exists. Move it aside before cleaning again."
            ) from exc
        except OSError as exc:
            raise ScrubError(
                f"Cannot publish output with an atomic, no-overwrite hard link: {exc.strerror}"
            ) from exc
    return destination, result


def _handler(path: Path, mode: str):
    if mode not in MODES:
        raise ScrubError(f"Unknown mode {mode!r}. Choose ai, privacy, or max.")
    handler = _HANDLERS.get(path.suffix.lower())
    if handler is None:
        raise ScrubError(
            "Unsupported file extension. Choose DOCX, PDF, JPEG, PNG, or WebP."
        )
    if not path.is_file():
        raise ScrubError("Input must be an existing regular file.")
    return import_module(f".{handler}", __name__)
