from pathlib import Path

import pikepdf
import pytest

from scrubmeta.common import ScrubError
from scrubmeta.pdf import clean, scan


def make_pdf(path: Path, *, author: str = "Alex", pages: int = 2) -> None:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(300, 400))
    pdf.docinfo["/Author"] = author
    pdf.docinfo["/Title"] = "Project report"
    pdf.docinfo["/Trapped"] = pikepdf.Name("/False")
    with pdf.open_metadata() as metadata:
        metadata["dc:creator"] = [author]
        metadata["dc:title"] = "Project report"
        metadata["xmp:CreatorTool"] = "Stable Diffusion"
        metadata["xmp:Description"] = "A synthetic test file"
    pdf.save(path)
    pdf.close()


def fields_by_name(fields):
    return {field.name: field for field in fields}


def test_scan_classifies_ai_privacy_and_technical_fields(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source)

    ai = fields_by_name(scan(source, "ai"))
    privacy = fields_by_name(scan(source, "privacy"))
    maximum = fields_by_name(scan(source, "max"))

    creator_tool = next(
        field for name, field in ai.items() if name.endswith("CreatorTool")
    )
    assert creator_tool.disposition == "removable"
    assert ai["DocumentInfo.Author"].disposition == "preserved"
    assert privacy["DocumentInfo.Author"].disposition == "removable"
    xmp_creator = next(
        field for name, field in privacy.items() if name.endswith("}creator")
    )
    assert xmp_creator.disposition == "removable"
    assert privacy["DocumentInfo.Title"].disposition == "removable"
    assert maximum["DocumentInfo.Title"].disposition == "removable"
    assert maximum["DocumentInfo.Trapped"].disposition == "preserved"


@pytest.mark.parametrize(
    ("mode", "expected_author", "expected_tool"),
    [
        ("ai", "Alex", None),
        ("privacy", None, None),
        ("max", None, None),
    ],
)
def test_clean_removes_selected_metadata_and_preserves_pdf_structure(
    tmp_path, mode, expected_author, expected_tool
):
    source = tmp_path / "source.pdf"
    output = tmp_path / "clean.pdf"
    make_pdf(source)
    original = source.read_bytes()

    clean(source, output, mode)

    assert source.read_bytes() == original
    with pikepdf.open(output) as pdf:
        assert len(pdf.pages) == 2
        assert pdf.docinfo.get("/Author") == expected_author
        with pdf.open_metadata() as metadata:
            assert metadata.get("xmp:CreatorTool") == expected_tool
            if mode == "ai":
                assert metadata.get("dc:creator") is not None
            else:
                assert metadata.get("dc:creator") is None


def test_clean_refuses_existing_output_and_preserves_it(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "clean.pdf"
    make_pdf(source)
    output.write_bytes(b"keep me")

    with pytest.raises(ScrubError, match="already exists"):
        clean(source, output, "privacy")

    assert output.read_bytes() == b"keep me"


def test_scan_rejects_non_pdf_and_unknown_mode(tmp_path):
    not_pdf = tmp_path / "not.pdf"
    not_pdf.write_text("not a PDF")

    with pytest.raises(ScrubError):
        scan(not_pdf, "privacy")
    with pytest.raises(ScrubError, match="mode"):
        scan(not_pdf, "unknown")


def test_clean_skips_pdfa_documents(tmp_path):
    source = tmp_path / "pdfa.pdf"
    output = tmp_path / "clean.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    with pdf.open_metadata() as metadata:
        metadata["pdfaid:part"] = "2"
        metadata["pdfaid:conformance"] = "B"
    pdf.save(source)
    pdf.close()

    with pytest.raises(ScrubError, match="PDF/A"):
        clean(source, output, "max")
    assert not output.exists()


def test_scan_reports_identifiable_c2pa_as_unsupported(tmp_path):
    source = tmp_path / "c2pa.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    with pdf.open_metadata() as metadata:
        metadata["xmp:Label"] = "C2PA Content Credentials"
    pdf.save(source)
    pdf.close()

    fields = scan(source, "max")
    assert any(
        field.name == "C2PA" and field.disposition == "unsupported" for field in fields
    )


def test_clean_refuses_c2pa_and_does_not_create_output(tmp_path):
    source = tmp_path / "c2pa.pdf"
    output = tmp_path / "clean.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    with pdf.open_metadata() as metadata:
        metadata["xmp:Label"] = "C2PA Content Credentials"
    pdf.save(source)
    pdf.close()

    with pytest.raises(ScrubError, match="C2PA"):
        clean(source, output, "max")
    assert not output.exists()


def test_clean_copies_byte_for_byte_when_no_fields_are_targeted(tmp_path):
    source = tmp_path / "empty.pdf"
    output = tmp_path / "clean.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(source)
    pdf.close()
    original = source.read_bytes()

    clean(source, output, "ai")

    assert output.read_bytes() == original


def test_clean_skips_signature_fields(tmp_path):
    source = tmp_path / "signed.pdf"
    output = tmp_path / "clean.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(
        {"/Fields": pikepdf.Array([pikepdf.Dictionary({"/FT": pikepdf.Name.Sig})])}
    )
    pdf.save(source)
    pdf.close()

    with pytest.raises(ScrubError, match="Signed"):
        clean(source, output, "max")
    assert not output.exists()


def test_clean_rejects_encrypted_pdf(tmp_path):
    source = tmp_path / "encrypted.pdf"
    output = tmp_path / "clean.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(source, encryption=pikepdf.Encryption(owner="owner", user="reader"))
    original = source.read_bytes()

    with pytest.raises(ScrubError, match="Encrypted"):
        clean(source, output, "privacy")
    assert source.read_bytes() == original
    assert not output.exists()
