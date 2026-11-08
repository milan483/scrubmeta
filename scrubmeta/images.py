"""Metadata inspection and removal for JPEG, PNG, and WebP images.

ExifTool performs metadata-only writes. Image pixels are never decoded or
re-encoded by this handler.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import Field, ScrubError, targeted

_SIGNATURES = {
    ".jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
    ".png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
    ".webp": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP",
}
_ESSENTIAL_TAGS = {
    "icc_profile",
    "iccprofile",
    "orientation",
    "imagesize",
    "imagewidth",
    "imageheight",
    "exifimagewidth",
    "exifimageheight",
    "colorspace",
    "compression",
    "encodingprocess",
    "bitspersample",
    "xresolution",
    "yresolution",
    "resolutionunit",
    "filter",
    "gamma",
    "chromaticity",
    "backgroundcolor",
    "transparency",
    "srgbintent",
    "significantbits",
    "pixelsperunitx",
    "pixelsperunity",
    "pixelunit",
    "jpegprocess",
    "jpegversion",
    "jpegquality",
    "samplesperpixel",
    "photometricinterpretation",
    "planarconfiguration",
    "ycbcrsubsampling",
    "ycbcrpositioning",
    "componentsconfiguration",
    "pixelydimension",
    "pixelxdimension",
    "imagepixeldepth",
    "imagepixelwidth",
    "imagepixelheight",
    "alpha",
    "bitdepth",
    "colortype",
    "interlace",
}
_PROVENANCE_MARKERS = ("c2pa", "jumbf", "contentcredentials", "content_credentials")
_SAFE_TAG = re.compile(r"^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$")
_PNG_WRITABLE_TAGS = {
    "title",
    "author",
    "description",
    "copyright",
    "creationtime",
    "software",
    "disclaimer",
    "pngwarning",
    "source",
    "comment",
    "collection",
    "artist",
    "document",
    "label",
    "make",
    "model",
    "parameters",
    "aestheticscore",
    "createdate",
    "moddate",
    "timestamp",
    "url",
    "xmp",
}


class _Pairs(dict[str, object]):
    """Dictionary that retains duplicate JSON metadata keys from ExifTool."""

    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        super().__init__(pairs)
        self.pairs = pairs


def _exiftool() -> str:
    executable = shutil.which("exiftool")
    if not executable:
        raise ScrubError(
            "ExifTool is required for image metadata support. Install ExifTool "
            "and ensure it is available on PATH."
        )
    return executable


def _run_exiftool(
    executable: str, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    """Run ExifTool with UTF-8 arguments from an ASCII-named argfile.

    ExifTool reads UTF-8 argument files reliably on Windows when filename
    decoding is enabled before ``-@``. Each argument occupies its own line.
    """
    if any("\n" in argument or "\r" in argument for argument in arguments):
        raise ScrubError("File paths containing newline characters are not supported")
    with tempfile.TemporaryDirectory(prefix="scrubmeta-") as workdir:
        args_path = Path(workdir) / "args.txt"
        args_path.write_text(
            "\n".join(arguments) + "\n", encoding="utf-8", newline="\n"
        )
        command = [
            executable,
            "-config",
            "",
            "-charset",
            "filename=utf8",
            "-@",
            args_path.name,
        ]
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=workdir,
        )


def _validate_signature(path: Path) -> None:
    suffix = path.suffix.lower()
    check = _SIGNATURES.get(suffix)
    if check is None:
        raise ScrubError(f"Unsupported image extension: {suffix or '(none)'}")
    try:
        with path.open("rb") as source:
            header = source.read(16)
    except OSError as exc:
        raise ScrubError(f"Cannot read image {path}: {exc}") from exc
    if not check(header):
        raise ScrubError(f"File signature does not match supported image type {suffix}")


def _read_metadata(path: Path) -> list[tuple[str, str]]:
    _validate_signature(path)
    path = path.resolve()
    executable = _exiftool()
    try:
        validation = _run_exiftool(
            executable, ["-validate", "-j", "-G1", "-s", "--", str(path)]
        )
    except OSError as exc:
        raise ScrubError(f"Could not run ExifTool: {exc}") from exc
    if validation.returncode != 0:
        raise ScrubError("ExifTool could not validate this image")
    try:
        validation_item = json.loads(validation.stdout)[0]
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        raise ScrubError("ExifTool returned invalid validation output") from exc
    if (
        validation_item.get("ExifTool:Validate", validation_item.get("Validate"))
        != "OK"
    ):
        raise ScrubError(
            "Image validation reported warnings; no metadata changes were made"
        )

    try:
        result = _run_exiftool(
            executable,
            ["-j", "-G1", "-a", "-s", "-api", "LargeFileSupport=1", "--", str(path)],
        )
    except OSError as exc:
        raise ScrubError(f"Could not run ExifTool: {exc}") from exc
    if result.returncode != 0:
        raise ScrubError("ExifTool could not safely read this image")
    try:
        payload = json.loads(result.stdout, object_pairs_hook=_Pairs)
    except (ValueError, TypeError) as exc:
        raise ScrubError("ExifTool returned invalid metadata output") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], _Pairs)
    ):
        raise ScrubError("ExifTool returned an unexpected metadata structure")
    item = payload[0]
    if "Error" in item:
        raise ScrubError("ExifTool reported an image read error")
    ignored_groups = {"exiftool", "system", "file", "composite"}
    return [
        (str(name), _value_text(value))
        for name, value in item.pairs
        if name != "SourceFile"
        and (
            str(name).split(":", 1)[0].casefold() not in ignored_groups
            or _is_provenance(str(name), _value_text(value))
        )
    ]


def _value_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _tag_name(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def _is_essential(name: str) -> bool:
    group = name.split(":", 1)[0].casefold().replace("-", "_")
    return (
        group.startswith("icc")
        or group == "jfif"
        or (group == "png" and _tag_name(name).casefold() == "profilename")
        or _tag_name(name).casefold() in _ESSENTIAL_TAGS
    )


def _is_unsupported_write(name: str) -> bool:
    group = name.split(":", 1)[0].casefold()
    tag = _tag_name(name).casefold()
    return group == "png" and tag not in _PNG_WRITABLE_TAGS and not _is_essential(name)


def _is_provenance(name: str, value: str) -> bool:
    combined = f"{name} {value}".casefold()
    return any(marker in combined for marker in _PROVENANCE_MARKERS)


def _is_privacy_tag(name: str, value: str) -> bool:
    key = _tag_name(name).casefold()
    terms = (
        "author",
        "artist",
        "creator",
        "owner",
        "copyright",
        "software",
        "hostcomputer",
        "date",
        "time",
        "gps",
        "location",
        "city",
        "country",
        "state",
        "province",
        "description",
        "comment",
        "caption",
        "subject",
        "keywords",
        "history",
        "serialnumber",
        "documentid",
        "instanceid",
        "originaldocumentid",
    )
    return any(term in key for term in terms) or targeted(value)


def _is_ai_tag(name: str, value: str) -> bool:
    key = _tag_name(name).casefold().replace("_", "").replace("-", "")
    # These names are specific to generation workflows. A generic EXIF
    # "Model" field is intentionally excluded because it usually names the
    # camera and is ordinary technical metadata.
    explicit = (
        "prompt",
        "negativeprompt",
        "workflow",
        "generationsoftware",
        "aigc",
        "parameters",
        "aestheticscore",
    )
    return (
        any(marker in key for marker in explicit) or targeted(name) or targeted(value)
    )


def scan(path: Path, mode: str) -> list[Field]:
    """Return image metadata fields with conservative mode-specific status."""
    if mode not in {"ai", "privacy", "max"}:
        raise ScrubError(f"Unknown clean mode: {mode}")
    metadata = _read_metadata(Path(path))
    fields: list[Field] = []
    for name, value in metadata:
        if (
            _is_provenance(name, value)
            or not _SAFE_TAG.fullmatch(name)
            or _is_unsupported_write(name)
        ):
            disposition = "unsupported"
        elif _is_essential(name):
            disposition = "preserved"
        elif mode == "ai":
            disposition = "removable" if _is_ai_tag(name, value) else "preserved"
        elif mode == "privacy":
            disposition = "removable" if _is_privacy_tag(name, value) else "preserved"
        else:
            disposition = "removable"
        fields.append(Field(name=name, disposition=disposition, value=value))
    return fields


def _deletion_args(fields: list[Field]) -> list[str]:
    args: list[str] = []
    seen: set[str] = set()
    for field in fields:
        if field.disposition != "removable":
            continue
        # ExifTool accepts group-qualified tags from -G1 output. Keep the group
        # so a same-named technical tag in another namespace is not deleted.
        tag = field.name
        if not _SAFE_TAG.fullmatch(tag):
            continue
        if tag not in seen:
            args.append(f"-{tag}=")
            seen.add(tag)
    return args


def clean(source: Path, output: Path, mode: str) -> None:
    """Write a metadata-cleaned copy without replacing either existing path."""
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ScrubError(f"Output already exists: {output}")
    if source == output.resolve():
        raise ScrubError("Image output must not replace the source")
    fields = scan(source, mode)
    if any(field.disposition == "unsupported" for field in fields):
        raise ScrubError(
            "Image contains unsupported or unwritable metadata and cannot be safely rewritten"
        )
    delete_args = _deletion_args(fields)
    created = False
    try:
        # ExifTool edits this private copy in place. This avoids its -o output
        # filename templates and keeps the user's source untouched.
        with source.open("rb") as src, output.open("xb") as dst:
            created = True
            shutil.copyfileobj(src, dst)
        if delete_args:
            result = _run_exiftool(
                _exiftool(), ["-overwrite_original", *delete_args, "--", str(output)]
            )
            if result.returncode != 0 or not output.exists():
                raise ScrubError("ExifTool could not write the cleaned image safely")
    except OSError as exc:
        if created:
            output.unlink(missing_ok=True)
        raise ScrubError("Could not write cleaned image") from exc
    except ScrubError:
        if created:
            output.unlink(missing_ok=True)
        raise
    try:
        _validate_signature(output)
        _read_metadata(output)
    except Exception as exc:
        output.unlink(missing_ok=True)
        if isinstance(exc, ScrubError):
            raise
        raise ScrubError(f"Cleaned image failed validation: {exc}") from exc
