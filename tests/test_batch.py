from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pikepdf
import pytest

from scrubmeta import batch
from scrubmeta.common import ScrubError


def make_pdf(
    path: Path, *, author: str | None = "Synthetic author", c2pa: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        if author is not None:
            pdf.docinfo["/Author"] = author
        if c2pa:
            with pdf.open_metadata(
                set_pikepdf_as_editor=False, update_docinfo=False
            ) as metadata:
                metadata["xmp:Label"] = "C2PA Content Credentials"
        pdf.save(path)


def summary(report: dict) -> dict[str, int]:
    return report["summary"]


def test_scan_nonrecursive_and_recursive_with_deterministic_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Mixed Ω Files"
    make_pdf(root / "z.pdf")
    (root / "a.txt").write_text("not supported", encoding="utf-8")
    make_pdf(root / "Nested" / "b.pdf")

    shallow, shallow_code = batch.run_batch(root, "scan", "privacy")
    recursive, recursive_code = batch.run_batch(root, "scan", "privacy", recursive=True)

    assert shallow_code == recursive_code == 0
    assert [row["path"] for row in shallow["results"]] == ["a.txt", "z.pdf"]
    assert [row["path"] for row in recursive["results"]] == [
        "a.txt",
        "Nested/b.pdf",
        "z.pdf",
    ]
    assert summary(shallow) == {
        "discovered": 2,
        "processed": 1,
        "cleaned": 0,
        "verified": 0,
        "skipped_unsupported": 1,
        "skipped_nonregular": 0,
        "remaining": 1,
        "unsupported": 0,
        "errors": 0,
    }
    assert shallow["results"][0]["fields"] == []
    author_field = shallow["results"][1]["fields"][0]
    assert author_field["name"] == "DocumentInfo.Author"
    assert "value" not in author_field
    values_report, _ = batch.run_batch(root, "scan", "privacy", show_values=True)
    assert values_report["results"][1]["fields"][0]["value"] == "Synthetic author"


def test_clean_recursive_preserves_relative_tree_and_source(tmp_path: Path) -> None:
    root = tmp_path / "Source Set"
    first = root / "one.pdf"
    second = root / "chapters" / "二.pdf"
    make_pdf(first)
    make_pdf(second)
    original_first, original_second = first.read_bytes(), second.read_bytes()

    report, code = batch.run_batch(root, "clean", "privacy", recursive=True)

    output_root = root.with_name("Source Set_clean")
    assert code == 0
    assert report["status"] == "cleaned"
    assert (output_root / "one.pdf").is_file()
    assert (output_root / "chapters" / "二.pdf").is_file()
    assert first.read_bytes() == original_first
    assert second.read_bytes() == original_second
    assert summary(report)["cleaned"] == 2
    assert [row["output"] for row in report["results"]] == [
        "chapters/二.pdf",
        "one.pdf",
    ]
    assert all(row["status"] == "cleaned" for row in report["results"])


def test_clean_explicit_output_dir_and_no_overwrite_collision_continues(
    tmp_path: Path,
) -> None:
    root = tmp_path / "input"
    make_pdf(root / "a.pdf")
    make_pdf(root / "b.pdf")
    destination = tmp_path / "output tree"
    (destination / "a.pdf").parent.mkdir(parents=True)
    (destination / "a.pdf").write_bytes(b"preserve collision")

    report, code = batch.run_batch(root, "clean", "privacy", output_dir=destination)

    assert code == 2
    assert report["status"] == "error"
    assert (destination / "a.pdf").read_bytes() == b"preserve collision"
    assert (destination / "b.pdf").is_file()
    assert summary(report)["cleaned"] == 1
    assert summary(report)["errors"] == 1
    assert report["results"][0]["status"] == "error"
    assert report["results"][1]["status"] == "cleaned"


@pytest.mark.parametrize("location", ["equal", "inside", "ancestor"])
def test_clean_rejects_overlapping_output_directory(
    tmp_path: Path, location: str
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    if location == "equal":
        output = root
    elif location == "inside":
        output = root / "out"
    else:
        output = tmp_path

    with pytest.raises(ScrubError, match="inside, equal to, or an ancestor"):
        batch.run_batch(root, "clean", output_dir=output)


def test_clean_rejects_symlink_output_component(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = tmp_path / "actual"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ScrubError, match="symlink component"):
        batch.run_batch(root, "clean", output_dir=link / "output")


def test_windows_reparse_point_attributes_are_treated_as_links(monkeypatch) -> None:
    reparse_flag = 0x400
    monkeypatch.setattr(
        batch.stat, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_flag, raising=False
    )
    fake_junction_stat = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o755,
        st_file_attributes=reparse_flag,
    )

    assert batch._is_reparse(fake_junction_stat)


def test_verify_aggregate_precedence_remaining_then_unsupported(tmp_path: Path) -> None:
    root = tmp_path / "verify"
    make_pdf(root / "a-remaining.pdf")
    make_pdf(root / "b-c2pa.pdf", author=None, c2pa=True)

    report, code = batch.run_batch(root, "verify", "privacy")

    assert code == 1
    assert report["status"] == "remaining"
    assert summary(report)["remaining"] == 1
    assert summary(report)["unsupported"] == 1
    assert summary(report)["verified"] == 2


def test_verify_errors_precede_remaining_and_unsupported(tmp_path: Path) -> None:
    root = tmp_path / "verify"
    make_pdf(root / "a-remaining.pdf")
    make_pdf(root / "b-c2pa.pdf", author=None, c2pa=True)
    (root / "c-corrupt.pdf").write_bytes(b"not a PDF")

    report, code = batch.run_batch(root, "verify", "privacy")

    assert code == 2
    assert report["status"] == "error"
    assert summary(report)["remaining"] == 1
    assert summary(report)["unsupported"] == 1
    assert summary(report)["errors"] == 1
    assert report["results"][-1]["status"] == "error"


def test_clean_corrupt_file_does_not_block_other_files_or_leave_partial_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    make_pdf(root / "a-good.pdf")
    bad = root / "b-bad.pdf"
    bad.write_bytes(b"corrupt")
    original = bad.read_bytes()

    report, code = batch.run_batch(root, "clean", "privacy")

    output_root = root.with_name("source_clean")
    assert code == 2
    assert (output_root / "a-good.pdf").is_file()
    assert not (output_root / "b-bad.pdf").exists()
    assert bad.read_bytes() == original
    assert summary(report)["cleaned"] == 1
    assert summary(report)["errors"] == 1


def test_publication_permission_failure_leaves_no_partial_output(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "source"
    source = root / "file.pdf"
    make_pdf(source)
    original = source.read_bytes()
    output_root = tmp_path / "out"
    original_link = os.link

    def deny_publication(staged: str | os.PathLike, destination: str | os.PathLike):
        if Path(destination).parent == output_root:
            raise PermissionError(13, "Permission denied", os.fspath(destination))
        return original_link(staged, destination)

    monkeypatch.setattr(batch.os, "link", deny_publication)

    report, code = batch.run_batch(root, "clean", output_dir=output_root)

    assert code == 2
    assert report["results"][0]["status"] == "error"
    assert "Permission denied" in report["results"][0]["error"]
    assert not (output_root / "file.pdf").exists()
    assert source.read_bytes() == original
    assert summary(report)["cleaned"] == 0
    assert summary(report)["errors"] == 1


def test_symlinks_are_reported_and_never_followed(tmp_path: Path) -> None:
    root = tmp_path / "source"
    target = tmp_path / "outside.pdf"
    target_dir = tmp_path / "outside-dir"
    make_pdf(root / "inside.pdf")
    make_pdf(target)
    make_pdf(target_dir / "hidden.pdf")
    try:
        (root / "linked.pdf").symlink_to(target)
        (root / "linked-dir").symlink_to(target_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    report, code = batch.run_batch(root, "scan", recursive=True)

    assert code == 0
    assert [row["path"] for row in report["results"]] == [
        "inside.pdf",
        "linked-dir",
        "linked.pdf",
    ]
    assert summary(report)["processed"] == 1
    assert summary(report)["skipped_nonregular"] == 2


def test_empty_directory_and_root_enumeration_error(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    report, code = batch.run_batch(root, "verify")
    assert code == 0
    assert report["status"] == "clean"
    assert summary(report)["discovered"] == 0
    clean_report, clean_code = batch.run_batch(root, "clean")
    assert clean_code == 0
    assert clean_report["status"] == "cleaned"
    assert not root.with_name("empty_clean").exists()

    def fail_scandir(_path):
        raise PermissionError("permission denied")

    monkeypatch.setattr(batch.os, "scandir", fail_scandir)
    with pytest.raises(ScrubError, match="Cannot enumerate directory"):
        batch.run_batch(root, "scan")


def test_dot_source_path_uses_directory_basename_for_clean_output(
    tmp_path: Path, monkeypatch
) -> None:
    make_pdf(tmp_path / "sample.pdf")
    monkeypatch.chdir(tmp_path)

    report, code = batch.run_batch(Path("."), "clean")

    assert code == 0
    assert report["status"] == "cleaned"
    assert (tmp_path.parent / f"{tmp_path.name}_clean" / "sample.pdf").is_file()


def test_nested_enumeration_error_is_per_entry_and_continues(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "source"
    make_pdf(root / "good.pdf")
    (root / "blocked").mkdir(parents=True)
    actual_scandir = os.scandir

    def selective_scandir(path):
        if Path(path) == root / "blocked":
            raise PermissionError("permission denied")
        return actual_scandir(path)

    monkeypatch.setattr(batch.os, "scandir", selective_scandir)

    report, code = batch.run_batch(root, "scan", recursive=True)

    assert code == 2
    assert [row["path"] for row in report["results"]] == ["blocked", "good.pdf"]
    assert report["results"][0]["status"] == "error"
    assert summary(report)["processed"] == 2
    assert summary(report)["errors"] == 1


def test_per_file_scan_failure_does_not_stop_batch(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "source"
    make_pdf(root / "a-fails.pdf")
    make_pdf(root / "b-good.pdf")
    actual_scan = batch.scan

    def selective_scan(path: Path, mode: str):
        if path.name == "a-fails.pdf":
            raise ScrubError("Synthetic permission error")
        return actual_scan(path, mode)

    monkeypatch.setattr(batch, "scan", selective_scan)

    report, code = batch.run_batch(root, "scan")

    assert code == 2
    assert report["results"][0]["status"] == "error"
    assert report["results"][1]["status"] == "scanned"
    assert summary(report)["errors"] == 1
    assert summary(report)["processed"] == 2


def test_default_output_supports_source_with_symlinked_ancestor(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    source = actual / "photos"
    make_pdf(source / "file.pdf")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")

    report, code = batch.run_batch(alias / "photos", "clean")

    assert code == 0
    assert report["summary"]["cleaned"] == 1
    assert (actual / "photos_clean" / "file.pdf").is_file()


def test_filesystem_root_clean_rejected_before_enumeration(tmp_path: Path) -> None:
    with pytest.raises(ScrubError, match="inside, equal to, or an ancestor"):
        batch.run_batch(Path(tmp_path.anchor), "clean")
