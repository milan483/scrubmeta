import json

import pikepdf
import pytest

from scrubmeta.cli import main


def make_pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.docinfo["/Author"] = "Private fixture author"
        pdf.save(path)


def test_directory_cli_roundtrip(tmp_path, capsys):
    source = tmp_path / "資料"
    path = source / "nested" / "test.PDF"
    make_pdf(path)
    original = path.read_bytes()
    assert main(["scan", str(source), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["processed"] == 0
    assert main(["scan", str(source), "--recursive", "--json"]) == 0
    report = capsys.readouterr().out
    assert "Private fixture author" not in report
    assert json.loads(report)["summary"]["processed"] == 1
    assert main(["verify", str(source), "--recursive", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["remaining"] == 1
    assert main(["clean", str(source), "--recursive", "--json"]) == 0
    cleaned = json.loads(capsys.readouterr().out)
    assert cleaned["summary"]["cleaned"] == 1
    output = tmp_path / "資料_clean"
    assert (output / "nested" / "test.PDF").is_file()
    assert path.read_bytes() == original
    assert main(["verify", str(output), "--recursive", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["verified"] == 1
    assert main(["clean", str(source), "--recursive", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["summary"]["errors"] == 1


def test_output_override_and_human_summary(tmp_path, capsys):
    source = tmp_path / "input"
    make_pdf(source / "test.pdf")
    output = tmp_path / "other"
    assert main(["clean", str(source), "--output-dir", str(output)]) == 0
    text = capsys.readouterr().out
    assert "cleaned=1" in text
    assert (output / "test.pdf").exists()


@pytest.mark.parametrize("option", [["--recursive"], ["--output-dir", "output"]])
def test_directory_options_reject_file(tmp_path, capsys, option):
    source = tmp_path / "test.pdf"
    make_pdf(source)
    assert main(["clean", str(source), *option, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_mixed_supported_formats_continue_past_unsafe_pdf(tmp_path, capsys):
    import shutil

    from PIL import Image
    from test_docx import make_docx

    if shutil.which("exiftool") is None:
        pytest.skip("ExifTool is unavailable")
    source = tmp_path / "mixed"
    source.mkdir()
    make_docx(source / "text.docx")
    make_pdf(source / "plain.pdf")
    for suffix, format_name in (("jpg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")):
        Image.new("RGB", (11, 7), (10, 90, 200)).save(
            source / f"image.{suffix}", format=format_name
        )
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(
            source / "encrypted.pdf",
            encryption=pikepdf.Encryption(owner="owner", user="reader"),
        )
    originals = {path.name: path.read_bytes() for path in source.iterdir()}
    assert main(["clean", str(source), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["cleaned"] == 5
    assert report["summary"]["errors"] == 1
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals
    output = tmp_path / "mixed_clean"
    assert not (output / "encrypted.pdf").exists()
    assert main(["verify", str(output), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["verified"] == 5
