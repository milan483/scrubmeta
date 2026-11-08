"""Metadata handling for OOXML Word documents (DOCX).

Only the three standard document-properties parts are inspected. Other ZIP
members are copied unchanged so the document body, styles, media, and
relationships remain intact.
"""

from __future__ import annotations

import os
import posixpath
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.dom import Node
from xml.dom.minidom import Document
from xml.parsers.expat import ExpatError

from defusedxml import minidom
from defusedxml.common import DefusedXmlException

from .common import Field, ScrubError, targeted

_CORE = "docProps/core.xml"
_APP = "docProps/app.xml"
_CUSTOM = "docProps/custom.xml"
_MAIN = "word/document.xml"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
_PROPERTY_ROOTS = {
    _CORE: (
        "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
        "coreProperties",
    ),
    _APP: (
        "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
        "Properties",
    ),
    _CUSTOM: (
        "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
        "Properties",
    ),
}

# Standard properties that carry personal, descriptive, software, or
# provenance information. Technical fields such as page and word counts are
# deliberately preserved in privacy mode.
_CORE_PRIVACY = {
    "category",
    "contentStatus",
    "creator",
    "created",
    "description",
    "identifier",
    "keywords",
    "language",
    "lastModifiedBy",
    "modified",
    "revision",
    "subject",
    "title",
    "version",
}
_APP_PRIVACY = {
    "Application",
    "AppVersion",
    "Company",
    "Manager",
    "Template",
    "TotalTime",
}
_MAX_APP_REMOVE = _APP_PRIVACY | {"HyperlinkBase"}
_APP_ALWAYS_PRESERVE = {
    "Pages",
    "Words",
    "Characters",
    "CharactersWithSpaces",
    "Lines",
    "Paragraphs",
    "HeadingPairs",
    "TitlesOfParts",
    "Hyperlinks",
    "HLinks",
    "DocSecurity",
    "ScaleCrop",
    "LinksUpToDate",
    "SharedDoc",
}
_MAX_XML_BYTES = 16 * 1024 * 1024
_MAX_DOCUMENT_XML_BYTES = 128 * 1024 * 1024


def _local(node: Node) -> str:
    return node.localName or node.nodeName.rsplit(":", 1)[-1]


def _children(node: Node) -> list[Node]:
    return [child for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE]


def _validate_mode(mode: str) -> None:
    if mode not in {"ai", "privacy", "max"}:
        raise ScrubError(f"Unsupported DOCX cleaning mode: {mode!r}")


def _open_docx(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ScrubError(f"Not a readable DOCX ZIP package: {path}") from exc

    try:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ScrubError("DOCX contains duplicate ZIP member names")
        if any(info.flag_bits & 0x1 for info in archive.infolist()):
            raise ScrubError("Encrypted DOCX packages are not supported")
        required = {"[Content_Types].xml", "_rels/.rels", _MAIN}
        if not required.issubset(names):
            raise ScrubError("ZIP file is missing required DOCX package parts")

        content_types = _read_xml(archive, "[Content_Types].xml")
        has_docx_type = any(
            node.getAttribute("ContentType")
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            for node in _children(content_types.documentElement)
            if node.namespaceURI == _CT_NS
            and _local(node) == "Override"
            and node.getAttribute("PartName") == "/word/document.xml"
        )
        if not has_docx_type:
            raise ScrubError("ZIP package is not a supported Word DOCX document")

        document = _read_xml(archive, _MAIN, _MAX_DOCUMENT_XML_BYTES).documentElement
        if (
            document.namespaceURI not in _WORD_NAMESPACES
            or _local(document) != "document"
            or not any(
                _local(child) == "body" and child.namespaceURI == document.namespaceURI
                for child in _children(document)
            )
        ):
            raise ScrubError("DOCX is missing a valid Word document body")
        rels = _read_xml(archive, "_rels/.rels")
        if any(
            "digital-signature" in relation.getAttribute("Type").lower()
            for relation in _children(rels.documentElement)
        ):
            raise ScrubError("Digitally signed DOCX packages are not supported")
        if any(name.lower().startswith("_xmlsignatures/") for name in names):
            raise ScrubError("Digitally signed DOCX packages are not supported")
        return archive
    except Exception:
        archive.close()
        raise


def _read_xml(
    archive: zipfile.ZipFile,
    name: str,
    max_bytes: int = _MAX_XML_BYTES,
) -> Document:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ScrubError(f"DOCX metadata part is missing: {name}") from exc
    if info.file_size > max_bytes:
        raise ScrubError(f"DOCX XML part is too large to inspect safely: {name}")
    try:
        data = archive.read(name)
        return minidom.parseString(data, forbid_dtd=True)
    except (
        OSError,
        RuntimeError,
        zipfile.BadZipFile,
        ExpatError,
        DefusedXmlException,
    ) as exc:
        raise ScrubError(f"Malformed DOCX XML part: {name}") from exc


def _metadata_parts(archive: zipfile.ZipFile) -> dict[str, Document]:
    parts: dict[str, Document] = {}
    for name in (_CORE, _APP, _CUSTOM):
        if name in archive.namelist():
            part = _read_xml(archive, name)
            root = part.documentElement
            if (root.namespaceURI, _local(root)) != _PROPERTY_ROOTS[name]:
                raise ScrubError(f"Unexpected DOCX properties structure in {name}")
            parts[name] = part
    return parts


def _unsupported_metadata_relationships(archive: zipfile.ZipFile) -> list[Field]:
    relationships = _read_xml(archive, "_rels/.rels")
    known_types = {
        "metadata/core-properties": _CORE,
        "extended-properties": _APP,
        "custom-properties": _CUSTOM,
    }
    unsupported: list[Field] = []
    for relation in _children(relationships.documentElement):
        relation_type = relation.getAttribute("Type").lower()
        expected = next(
            (
                path
                for suffix, path in known_types.items()
                if relation_type.endswith(suffix)
            ),
            None,
        )
        if expected is None:
            continue
        target = relation.getAttribute("Target")
        normalized = posixpath.normpath(target.lstrip("/")) if target else ""
        if (
            relation.getAttribute("TargetMode").lower() == "external"
            or normalized != expected
        ):
            unsupported.append(
                Field(
                    f"{expected}: noncanonical relationship",
                    "unsupported",
                    "Noncanonical document-properties relationship; this metadata location is not inspected",
                )
            )
    return unsupported


def _text_value(element: Node) -> str:
    values: list[str] = []
    pending = [element]
    while pending:
        node = pending.pop()
        if node.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
            text = (node.nodeValue or "").strip()
            if text:
                values.append(text)
        else:
            pending.extend(reversed(node.childNodes))
    return " ".join(values)


def _is_removable(part: str, field_name: str, value: str, mode: str) -> bool:
    if not value:
        return False
    if part == _APP and field_name in _APP_ALWAYS_PRESERVE:
        return False
    if mode == "ai":
        return targeted(value) or (part == _CUSTOM and targeted(field_name))
    if mode == "privacy":
        if part == _CORE:
            return field_name in _CORE_PRIVACY
        if part == _APP:
            return field_name in _APP_PRIVACY
        privacy_name = field_name.lower()
        return part == _CUSTOM and (
            targeted(value)
            or any(
                token in privacy_name
                for token in (
                    "author",
                    "creator",
                    "software",
                    "timestamp",
                    "date",
                    "location",
                    "description",
                    "prompt",
                    "model",
                    "provenance",
                    "generated",
                    "user",
                )
            )
        )
    if part == _APP:
        return field_name in _MAX_APP_REMOVE
    return part in {_CORE, _CUSTOM}


def _fields(parts: dict[str, Document], mode: str) -> list[Field]:
    fields: list[Field] = []
    for part, root in parts.items():
        for node in _children(root.documentElement):
            if part == _CUSTOM:
                name = node.getAttribute("name") or _local(node)
                value = _text_value(node)
                field_name = f"custom.{name}"
            else:
                name = _local(node)
                value = _text_value(node)
                field_name = name
            if not value:
                continue
            disposition = (
                "removable" if _is_removable(part, name, value, mode) else "preserved"
            )
            fields.append(Field(f"{part}:{field_name}", disposition, value))
    return fields


def scan(path: Path, mode: str) -> list[Field]:
    """Return metadata fields in a DOCX and their disposition for ``mode``."""
    _validate_mode(mode)
    path = Path(path)
    if not path.is_file():
        raise ScrubError(f"DOCX file does not exist or is not a regular file: {path}")
    with _open_docx(path) as archive:
        return _fields(
            _metadata_parts(archive), mode
        ) + _unsupported_metadata_relationships(archive)


def _remove_fields(parts: dict[str, Document], mode: str) -> dict[str, bytes]:
    changed: dict[str, bytes] = {}
    for part, root in parts.items():
        removed = False
        root_element = root.documentElement
        for node in _children(root_element):
            if part == _CUSTOM:
                name = node.getAttribute("name") or _local(node)
            else:
                name = _local(node)
            value = _text_value(node)
            if _is_removable(part, name, value, mode):
                root_element.removeChild(node)
                removed = True
        if removed:
            # DOM serialization retains prefixes and namespace declarations,
            # including QName values such as xsi:type="dcterms:W3CDTF".
            changed[part] = root.toxml(encoding="utf-8")
    return changed


def clean(source: Path, output: Path, mode: str) -> None:
    """Write a cleaned DOCX atomically, preserving untouched package members."""
    _validate_mode(mode)
    source = Path(source)
    output = Path(output)
    if source.resolve() == output.resolve():
        raise ScrubError("DOCX output must not be the source file")
    if output.exists():
        raise ScrubError(f"Output already exists: {output}")
    if not source.is_file():
        raise ScrubError(f"DOCX file does not exist or is not a regular file: {source}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with _open_docx(source) as archive:
            unsupported = _unsupported_metadata_relationships(archive)
            if unsupported:
                raise ScrubError(
                    unsupported[0].value or "Unsupported DOCX metadata location"
                )
            parts = _metadata_parts(archive)
            replacements = _remove_fields(parts, mode)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
            )
            os.close(fd)
            temp_path = Path(temp_name)
            with zipfile.ZipFile(temp_path, "w") as result:
                result.comment = archive.comment
                if not replacements:
                    # Preserve a no-op input exactly, including ZIP metadata.
                    result.close()
                    shutil.copyfile(source, temp_path)
                else:
                    for info in archive.infolist():
                        payload = replacements.get(info.filename)
                        if payload is None:
                            payload = archive.read(info.filename)
                        result.writestr(info, payload)

        # Validate the completed package before exposing it at the requested path.
        with _open_docx(temp_path) as cleaned:
            _metadata_parts(cleaned)
        os.link(temp_path, output)
        temp_path.unlink()
        temp_path = None
    except FileExistsError as exc:
        raise ScrubError(f"Output already exists: {output}") from exc
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise ScrubError(f"Could not write DOCX output {output}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
