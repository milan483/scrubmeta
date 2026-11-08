import json
import os
from pathlib import Path

import pikepdf
import pytest

import scrubmeta
from scrubmeta.cli import main
from scrubmeta.common import Field, ScrubError


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "例 metadata.PDF"
    with pikepdf.new() as document:
        document.add_blank_page()
        document.docinfo["/Author"] = "Synthetic private author"
        document.save(path)
    return path


def test_end_to_end(source, capsys):
    original = source.read_bytes()
    assert main(["scan", str(source), "--json"]) == 0
    report = capsys.readouterr().out
    assert "Synthetic private author" not in report
    assert json.loads(report)["readable"]
    assert main(["scan", str(source), "--show-values", "--json"]) == 0
    assert "Synthetic private author" in capsys.readouterr().out
    assert main(["verify", str(source), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "remaining"

    assert main(["clean", str(source), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    output = Path(report["output"])
    assert output == source.with_name("例 metadata_clean.PDF")
    assert report["status"] == "clean"
    assert source.read_bytes() == original
    assert main(["verify", str(output), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "clean"
    assert main(["clean", str(source), "--json"]) == 2
    assert "already exists" in json.loads(capsys.readouterr().out)["error"]
    assert set(source.parent.iterdir()) == {source, output}


def test_output_created_during_clean_is_never_replaced(source, monkeypatch):
    publish = os.link
    destination = source.with_stem(source.stem + "_clean")

    def concurrent_create(staged_file, path):
        if path == destination:
            path.write_bytes(b"another writer")
        publish(staged_file, path)

    monkeypatch.setattr(os, "link", concurrent_create)
    with pytest.raises(ScrubError, match="already exists"):
        scrubmeta.clean(source)
    assert destination.read_bytes() == b"another writer"
    assert set(source.parent.iterdir()) == {source, destination}


def test_cannot_publish(source, monkeypatch):
    original = source.read_bytes()

    publish = os.link

    def deny(staged_file, destination):
        if destination.parent == source.parent:
            raise PermissionError(13, "Permission denied")
        publish(staged_file, destination)

    monkeypatch.setattr(os, "link", deny)
    with pytest.raises(ScrubError, match="Permission denied"):
        scrubmeta.clean(source)
    assert source.read_bytes() == original
    assert set(source.parent.iterdir()) == {source}


def test_wrong_extension(tmp_path, capsys):
    path = tmp_path / "source.txt"
    path.write_text("test", encoding="utf-8")
    assert main(["verify", str(path), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_verify_distinguishes_unsupported_from_clean(source, monkeypatch, capsys):
    monkeypatch.setattr(
        scrubmeta, "scan", lambda *args: [Field("content credentials", "unsupported")]
    )
    assert main(["verify", str(source), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported/unknown"
