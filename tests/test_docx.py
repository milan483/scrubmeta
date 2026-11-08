from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scrubmeta.common import ScrubError
from scrubmeta.docx import clean, scan

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CORE_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"


def make_docx(path: Path, *, custom_xml: bytes | None = None) -> dict[str, bytes]:
    parts = {
        "[Content_Types].xml": (
            f'<Types xmlns="{CT_NS}">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ).encode(),
        "_rels/.rels": (
            f'<Relationships xmlns="{REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>'
            "</Relationships>"
        ).encode(),
        "word/document.xml": b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Keep this prose</w:t></w:r></w:p></w:body></w:document>',
        "word/styles.xml": b"<styles>intentionally opaque payload</styles>",
        "word/media/image1.png": b"synthetic-media",
        "docProps/core.xml": (
            f'<cp:coreProperties xmlns:cp="{CORE_NS}" xmlns:dc="{DC_NS}" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:creator>Ada Author</dc:creator>"
            "<dc:title>Quarterly Report</dc:title>"
            "<dc:description>Generated with GPT-4 in a local workflow</dc:description>"
            '<dcterms:created xsi:type="dcterms:W3CDTF">2024-01-02T03:04:05Z</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">2024-01-03T03:04:05Z</dcterms:modified>'
            "<cp:lastModifiedBy>Grace</cp:lastModifiedBy>"
            "<cp:revision>7</cp:revision>"
            "</cp:coreProperties>"
        ).encode(),
        "docProps/app.xml": (
            f'<Properties xmlns="{EP_NS}">'
            "<Application>Word Processor 9</Application>"
            "<Company>Example Ltd</Company>"
            "<Pages>3</Pages><Words>120</Words>"
            "<HeadingPairs>Generated with GPT-4, but structural</HeadingPairs>"
            "</Properties>"
        ).encode(),
    }
    if custom_xml is None:
        custom_xml = (
            f'<Properties xmlns="{CUSTOM_NS}" xmlns:vt="{VT_NS}">'
            '<property name="Model" pid="2" fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"><vt:lpwstr>OpenAI o3</vt:lpwstr></property>'
            '<property name="Department" pid="3" fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"><vt:lpwstr>Research</vt:lpwstr></property>'
            "</Properties>"
        ).encode()
    parts["docProps/custom.xml"] = custom_xml
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return parts


def values(path: Path, mode: str) -> dict[str, str | None]:
    return {field.name: field.value for field in scan(path, mode)}


def replace_part(path: Path, part: str, payload: bytes) -> None:
    temporary = path.with_suffix(".rewrite")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as target:
        target.comment = source.comment
        for info in source.infolist():
            target.writestr(
                info, payload if info.filename == part else source.read(info.filename)
            )
    temporary.replace(path)


def test_scan_classifies_metadata_for_ai_and_privacy_modes(tmp_path: Path) -> None:
    path = tmp_path / "source.docx"
    make_docx(path)

    ai = {field.name: field.disposition for field in scan(path, "ai")}
    privacy = {field.name: field.disposition for field in scan(path, "privacy")}

    assert ai["docProps/core.xml:creator"] == "preserved"
    assert ai["docProps/core.xml:description"] == "removable"
    assert ai["docProps/custom.xml:custom.Model"] == "removable"
    assert ai["docProps/custom.xml:custom.Department"] == "preserved"
    assert ai["docProps/app.xml:HeadingPairs"] == "preserved"
    assert privacy["docProps/core.xml:creator"] == "removable"
    assert privacy["docProps/app.xml:Application"] == "removable"
    assert privacy["docProps/app.xml:Pages"] == "preserved"
    assert privacy["docProps/custom.xml:custom.Department"] == "preserved"


def test_ai_clean_removes_only_recognized_ai_values_and_preserves_parts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "clean.docx"
    originals = make_docx(source)

    clean(source, output, "ai")

    remaining = values(output, "ai")
    assert "docProps/core.xml:creator" in remaining
    assert "docProps/core.xml:description" not in remaining
    assert "docProps/custom.xml:custom.Model" not in remaining
    assert "docProps/custom.xml:custom.Department" in remaining
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert after.namelist() == before.namelist()
        for name in originals:
            if name not in {"docProps/core.xml", "docProps/custom.xml"}:
                assert after.read(name) == before.read(name)
        assert b"Keep this prose" in after.read("word/document.xml")
        assert after.read("word/media/image1.png") == b"synthetic-media"
        rewritten_core = after.read("docProps/core.xml")
        assert b'xsi:type="dcterms:W3CDTF"' in rewritten_core
        assert b'xmlns:dcterms="http://purl.org/dc/terms/"' in rewritten_core


def test_privacy_then_max_clean_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    privacy = tmp_path / "privacy.docx"
    maximum = tmp_path / "maximum.docx"
    make_docx(source)

    clean(source, privacy, "privacy")
    privacy_values = values(privacy, "privacy")
    assert not any(
        name.endswith((":creator", ":Application")) for name in privacy_values
    )
    assert "docProps/app.xml:Pages" in privacy_values
    assert "docProps/custom.xml:custom.Department" in privacy_values

    clean(source, maximum, "max")
    max_values = values(maximum, "max")
    assert "docProps/app.xml:Pages" in max_values
    assert "docProps/app.xml:Application" not in max_values
    assert all(field.disposition != "removable" for field in scan(maximum, "max"))

    idempotent = tmp_path / "idempotent.docx"
    clean(privacy, idempotent, "privacy")
    assert values(idempotent, "privacy") == privacy_values


def test_clean_rejects_existing_output_and_same_source(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)
    with pytest.raises(ScrubError, match="must not be the source"):
        clean(source, source, "privacy")
    output = tmp_path / "occupied.docx"
    output.write_bytes(b"keep")
    with pytest.raises(ScrubError, match="already exists"):
        clean(source, output, "privacy")
    assert output.read_bytes() == b"keep"


def test_scan_rejects_non_docx_and_malformed_properties(tmp_path: Path) -> None:
    not_docx = tmp_path / "nope.docx"
    not_docx.write_bytes(b"not a ZIP")
    with pytest.raises(ScrubError):
        scan(not_docx, "privacy")

    malformed = tmp_path / "malformed.docx"
    make_docx(
        malformed,
        custom_xml=b"<!DOCTYPE x [<!ENTITY x 'expanded'>]><Properties>&x;</Properties>",
    )
    with pytest.raises(ScrubError, match="Malformed DOCX XML"):
        scan(malformed, "privacy")

    malformed_body = tmp_path / "malformed-body.docx"
    make_docx(malformed_body)
    replace_part(malformed_body, "word/document.xml", b"<w:document>")
    with pytest.raises(ScrubError, match="Malformed DOCX XML part: word/document.xml"):
        scan(malformed_body, "privacy")


def test_scan_and_clean_reject_signed_docx(tmp_path: Path) -> None:
    path = tmp_path / "signed.docx"
    make_docx(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("_xmlsignatures/sig1.xml", b"signature")
    with pytest.raises(ScrubError, match="Digitally signed"):
        scan(path, "privacy")


def test_no_target_clean_is_byte_for_byte_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "copy.docx"
    make_docx(source)
    # The fixture has metadata, so clean it once and use that result as input.
    intermediate = tmp_path / "intermediate.docx"
    clean(source, intermediate, "max")

    clean(intermediate, output, "max")

    assert output.read_bytes() == intermediate.read_bytes()


def test_noncanonical_property_relationship_is_reported_and_blocks_clean(
    tmp_path: Path,
) -> None:
    source = tmp_path / "alternate.docx"
    make_docx(source)
    with zipfile.ZipFile(source) as archive:
        relationships = archive.read("_rels/.rels").replace(
            b'Target="docProps/core.xml"', b'Target="custom/core.xml"'
        )
    replace_part(source, "_rels/.rels", relationships)

    unsupported = [
        field for field in scan(source, "privacy") if field.disposition == "unsupported"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].name == "docProps/core.xml: noncanonical relationship"
    with pytest.raises(ScrubError, match="Noncanonical document-properties"):
        clean(source, tmp_path / "clean.docx", "privacy")


def test_scan_rejects_well_formed_xml_without_word_body(tmp_path):
    source = tmp_path / "invalid-body.docx"
    make_docx(source)
    replace_part(source, "word/document.xml", b"<not-a-word-document/>")
    with pytest.raises(ScrubError, match="Word document body"):
        scan(source, "privacy")
