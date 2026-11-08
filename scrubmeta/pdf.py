"""PDF metadata inspection and sanitization using pikepdf.

Only DocumentInfo and XMP metadata are edited. Page content and the rest of
the PDF object graph are left to pikepdf's normal rewrite operation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pikepdf

from .common import Field, ScrubError, targeted

_PRIVACY_TERMS = (
    "author",
    "creator",
    "producer",
    "software",
    "date",
    "time",
    "subject",
    "title",
    "keyword",
    "description",
    "location",
    "gps",
    "provenance",
    "history",
    "rights",
    "owner",
    "email",
    "user",
    "prompt",
    "model",
    "workflow",
    "generation",
    "trainedalgorithmicmedia",
)
_TECHNICAL_DOCINFO = {
    "/Trapped",
    "/PDFVersion",
    "/GTS_PDFXVersion",
    "/GTS_PDFXConformance",
}
_TECHNICAL_XMP_MARKERS = (
    "pdfa/ns/id/",
    "pdfx/ns/id/",
    "pdfxid/ns/id/",
    "pdfuaid/ns/id/",
    "/PDFVersion",
    "/GTS_PDFXVersion",
    "/GTS_PDFXConformance",
)
_KNOWN_XMP_URIS = (
    "http://ns.adobe.com/xap/1.0/",
    "http://purl.org/dc/elements/1.1/",
    "http://ns.adobe.com/pdf/1.3/",
    "http://ns.adobe.com/xap/1.0/rights/",
    "http://ns.adobe.com/xap/1.0/mm/",
    "http://ns.adobe.com/photoshop/1.0/",
    "http://ns.adobe.com/tiff/1.0/",
    "http://ns.adobe.com/exif/1.0/",
    "http://purl.org/dc/terms/",
)
_SIGNATURE_FIELD = "/FT"
_C2PA_TERMS = ("c2pa", "content credentials", "contentcredentials")


def _text(value: Any) -> str:
    try:
        return str(value)
    except (TypeError, ValueError, RuntimeError, pikepdf.PdfError):
        return "<unreadable value>"


def _open(path: Path) -> pikepdf.Pdf:
    try:
        return pikepdf.open(path, attempt_recovery=False)
    except pikepdf.PasswordError as exc:
        raise ScrubError(
            "Encrypted PDFs are not supported; no changes were made."
        ) from exc
    except Exception as exc:
        raise ScrubError("Could not read a valid PDF file.") from exc


def _signature_present(pdf: pikepdf.Pdf) -> bool:
    """Return true for AcroForm signature fields or signature dictionaries."""
    try:
        root = pdf.Root
        if root.get("/Perms") is not None:
            return True
        form = root.get("/AcroForm")
        if form is None:
            return False
        pending = list(form.get("/Fields", []))
        seen: set[tuple[int, int]] = set()
        while pending:
            item = pending.pop()
            try:
                identity = item.objgen
                if identity != (0, 0) and identity in seen:
                    continue
                if identity != (0, 0):
                    seen.add(identity)
                if len(seen) > 100_000:
                    return True
                signature = item.get(_SIGNATURE_FIELD) == pikepdf.Name.Sig
                value = item.get("/V")
                if value is not None:
                    try:
                        signature = signature or value.get("/Type") == pikepdf.Name.Sig
                    except AttributeError:
                        pass
            except (AttributeError, TypeError):
                return True
            if signature:
                return True
            pending.extend(item.get("/Kids", []))
    except (AttributeError, TypeError, pikepdf.PdfError):
        # A malformed form is not safe to rewrite.
        return True
    return False


def _pdfa(pdf: pikepdf.Pdf) -> bool:
    with pdf.open_metadata(
        set_pikepdf_as_editor=False, update_docinfo=False, strict=True
    ) as metadata:
        return bool(metadata.get("pdfaid:part") or metadata.get("pdfaid:conformance"))


def _conformance(pdf: pikepdf.Pdf) -> str | None:
    if any(key in pdf.docinfo for key in ("/GTS_PDFXVersion", "/GTS_PDFXConformance")):
        return "PDF/X"
    with pdf.open_metadata(
        set_pikepdf_as_editor=False, update_docinfo=False, strict=True
    ) as metadata:
        names = [str(key).lower() for key in metadata]
        if any("pdfuaid/ns/id/" in name for name in names):
            return "PDF/UA"
        if any("pdfxid/ns/id/" in name or "pdfx/ns/id/" in name for name in names):
            return "PDF/X"
    if _pdfa(pdf):
        return "PDF/A"
    return None


def _hazard(pdf: pikepdf.Pdf) -> str | None:
    if pdf.is_encrypted:
        return "Encrypted PDFs are not supported; no changes were made."
    if _signature_present(pdf):
        return "Signed PDFs are skipped because rewriting would invalidate signatures."
    try:
        conformance = _conformance(pdf)
        if conformance:
            return f"{conformance} documents are skipped to avoid breaking conformance."
    except Exception:  # noqa: BLE001 - malformed XMP must fail closed
        return "Could not safely inspect PDF XMP metadata; no changes were made."
    return None


def _xmp_fields(pdf: pikepdf.Pdf) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    with pdf.open_metadata(
        set_pikepdf_as_editor=False, update_docinfo=False, strict=True
    ) as metadata:
        for key in metadata:
            value = metadata[key]
            fields.append((str(key), _text(value)))
    return fields


def _xmp_kind(name: str) -> str:
    if any(marker.lower() in name.lower() for marker in _TECHNICAL_XMP_MARKERS):
        return "technical"
    namespace = name.removeprefix("{").partition("}")[0].rstrip("/")
    if any(uri.rstrip("/") == namespace for uri in _KNOWN_XMP_URIS):
        return "supported"
    return "unsupported"


def _removable(name: str, value: str, mode: str, *, docinfo: bool = False) -> bool:
    if docinfo:
        if name in _TECHNICAL_DOCINFO:
            return False
    elif _xmp_kind(name) != "supported":
        return False
    local_name = name.rsplit("}", 1)[-1]
    if mode == "ai":
        return local_name.lstrip("/").casefold() == "prompt" or targeted(
            f"{local_name} {value}"
        )
    if mode == "privacy":
        return any(term in local_name.lower() for term in _PRIVACY_TERMS)
    return mode == "max"


def _collect(pdf: pikepdf.Pdf, mode: str) -> list[Field]:
    result: list[Field] = []
    for key, raw_value in pdf.docinfo.items():
        name, value = str(key), _text(raw_value)
        disposition = (
            "removable" if _removable(name, value, mode, docinfo=True) else "preserved"
        )
        result.append(Field(f"DocumentInfo.{name.lstrip('/')}", disposition, value))
    for key, value in _xmp_fields(pdf):
        if _xmp_kind(key) == "unsupported":
            disposition = "unsupported"
        else:
            disposition = "removable" if _removable(key, value, mode) else "preserved"
        result.append(Field(f"XMP.{key}", disposition, value))

    # C2PA packets are not understood or edited by this handler. Surface the
    # limitation whenever an identifiable marker is present.
    if any(
        term in f"{field.name} {field.value or ''}".lower()
        for field in result
        for term in _C2PA_TERMS
    ):
        result.append(
            Field("C2PA", "unsupported", "C2PA metadata is not inspected or modified.")
        )
    return result


def scan(path: Path, mode: str) -> list[Field]:
    """List PDF metadata and classify it for the requested mode."""
    source = Path(path)
    if mode not in {"ai", "privacy", "max"}:
        raise ScrubError(f"Unknown PDF cleaning mode: {mode}")
    if not source.is_file():
        raise ScrubError(f"PDF does not exist or is not a file: {source}")
    pdf = _open(source)
    try:
        fields = _collect(pdf, mode)
        page_objgens = [page.objgen for page in pdf.pages]
        if len(page_objgens) != len(set(page_objgens)):
            raise ScrubError("PDF page tree references a page more than once.")
        if pdf.is_encrypted:
            fields.append(
                Field(
                    "PDF.encryption",
                    "unsupported",
                    "Encrypted PDF metadata may be inaccessible.",
                )
            )
        if _signature_present(pdf):
            fields.append(
                Field(
                    "PDF.signature",
                    "unsupported",
                    "Signed PDFs cannot be safely rewritten.",
                )
            )
        conformance = _conformance(pdf)
        if conformance:
            fields.append(
                Field(
                    "PDF.conformance",
                    "unsupported",
                    f"{conformance} conformance will not be modified.",
                )
            )
        return fields
    except ScrubError:
        raise
    except Exception as exc:
        raise ScrubError(
            "Could not safely inspect PDF metadata and page structure."
        ) from exc
    finally:
        pdf.close()


def clean(source: Path, output: Path, mode: str) -> None:
    """Write a sanitized PDF to a new path, preserving the source."""
    source, output = Path(source), Path(output)
    if mode not in {"ai", "privacy", "max"}:
        raise ScrubError(f"Unknown PDF cleaning mode: {mode}")
    if not source.is_file():
        raise ScrubError(f"PDF does not exist or is not a file: {source}")
    if output.exists():
        raise ScrubError(f"Output already exists: {output}")
    pdf = _open(source)
    temp_name: str | None = None
    original_pages = 0
    try:
        original_pages = len(pdf.pages)
        hazard = _hazard(pdf)
        if hazard:
            raise ScrubError(hazard)
        if any(field.name == "C2PA" for field in _collect(pdf, mode)):
            raise ScrubError(
                "C2PA content credentials are unsupported; PDF was not rewritten."
            )

        remove_docinfo = [
            key
            for key, value in pdf.docinfo.items()
            if _removable(str(key), _text(value), mode, docinfo=True)
        ]
        with pdf.open_metadata(
            set_pikepdf_as_editor=False, update_docinfo=False, strict=True
        ) as metadata:
            remove_xmp = [
                key
                for key in metadata
                if _removable(str(key), _text(metadata[key]), mode)
            ]
        if not remove_docinfo and not remove_xmp:
            pdf.close()
            output.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as original, output.open("xb") as copy:
                shutil.copyfileobj(original, copy)
            return

        for key in remove_docinfo:
            del pdf.docinfo[key]

        if remove_xmp:
            with pdf.open_metadata(
                set_pikepdf_as_editor=False, update_docinfo=False, strict=True
            ) as metadata:
                for key in remove_xmp:
                    del metadata[key]

        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        os.close(fd)
        pdf.save(temp_name, fix_metadata_version=False)
        pdf.close()

        # Read back before publishing the output. Use hard-link creation for
        # atomic no-overwrite semantics on the same filesystem.
        check = _open(Path(temp_name))
        try:
            if len(check.pages) != original_pages:
                raise ScrubError("PDF page count changed during cleaning.")
        finally:
            check.close()
        os.link(temp_name, output)
        os.unlink(temp_name)
        temp_name = None
    except ScrubError:
        raise
    except FileExistsError as exc:
        raise ScrubError(f"Output already exists: {output}") from exc
    except Exception as exc:
        raise ScrubError("Could not safely write or validate the cleaned PDF.") from exc
    finally:
        try:
            pdf.close()
        except pikepdf.PdfError:
            pass
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
