from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageCms
from PIL.PngImagePlugin import PngInfo

from scrubmeta import images as image_handler
from scrubmeta.common import ScrubError
from scrubmeta.images import clean, scan

EXIFTOOL = shutil.which("exiftool")
pytestmark = pytest.mark.skipif(EXIFTOOL is None, reason="ExifTool is not installed")


def _make_image(path: Path, suffix: str) -> None:
    image = Image.new("RGB", (11, 7), (24, 80, 140))
    image.save(path, format=suffix.upper() if suffix != "jpg" else "JPEG")


def _set_tags(path: Path, *args: str) -> None:
    assert EXIFTOOL
    result = subprocess.run(
        [EXIFTOOL, "-overwrite_original", *args, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("suffix", ["jpg", "png", "webp"])
def test_privacy_clean_removes_author_and_software_without_changing_pixels(
    tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / f"sample.{suffix}"
    output = tmp_path / f"clean.{suffix}"
    _make_image(source, suffix)
    _set_tags(
        source,
        "-XMP-dc:Creator=Alice",
        "-XMP-xmp:CreatorTool=Photo Editor",
        "-XMP-dc:Description=Family vacation",
    )
    source_hash = _digest(source)
    original_pixels = Image.open(source).convert("RGB").tobytes()

    fields = scan(source, "privacy")
    assert {
        field.name.rsplit(":", 1)[-1]
        for field in fields
        if field.disposition == "removable"
    } >= {"Creator", "CreatorTool", "Description"}
    clean(source, output, "privacy")

    assert _digest(source) == source_hash
    cleaned_fields = scan(output, "privacy")
    assert not [field for field in cleaned_fields if field.disposition == "removable"]
    assert Image.open(output).convert("RGB").tobytes() == original_pixels


def test_ai_mode_removes_prompt_but_preserves_non_ai_description(
    tmp_path: Path,
) -> None:
    source = tmp_path / "generated.png"
    output = tmp_path / "generated_clean.png"
    _make_image(source, "png")
    _set_tags(
        source,
        "-XMP-dc:Description=Stable Diffusion: soft watercolor landscape",
        "-XMP-dc:Rights=All rights reserved",
    )

    fields = {field.name: field.disposition for field in scan(source, "ai")}
    assert fields.get("XMP-dc:Description") == "removable"
    assert fields.get("XMP-dc:Rights") == "preserved"
    clean(source, output, "ai")
    remaining = {field.name for field in scan(output, "ai")}
    assert "XMP-dc:Description" not in remaining
    assert "XMP-dc:Rights" in remaining


def test_ai_mode_preserves_camera_model(tmp_path: Path) -> None:
    source = tmp_path / "camera.jpg"
    output = tmp_path / "camera_clean.jpg"
    _make_image(source, "jpg")
    _set_tags(source, "-IFD0:Model=Canon EOS R6", "-XMP-xmp:CreatorTool=GPT-6")

    fields = {field.name: field for field in scan(source, "ai")}
    assert fields["IFD0:Model"].disposition == "preserved"
    assert fields["XMP-xmp:CreatorTool"].disposition == "removable"

    clean(source, output, "ai")

    remaining = {field.name: field for field in scan(output, "ai")}
    assert remaining["IFD0:Model"].value == "Canon EOS R6"
    assert "XMP-xmp:CreatorTool" not in remaining


def test_max_preserves_orientation_and_rejects_mismatched_signature(
    tmp_path: Path,
) -> None:
    source = tmp_path / "oriented.jpg"
    output = tmp_path / "oriented_clean.jpg"
    _make_image(source, "jpg")
    _set_tags(source, "-IFD0:Orientation#=6", "-XMP-dc:Creator=Alice")
    before = {field.name: field.value for field in scan(source, "max")}
    assert before.get("IFD0:Orientation") == "Rotate 90 CW"

    clean(source, output, "max")

    after = {field.name: field.value for field in scan(output, "max")}
    assert after.get("IFD0:Orientation") == "Rotate 90 CW"
    assert not [
        field for field in scan(output, "privacy") if field.name.endswith(":Creator")
    ]

    fake = tmp_path / "fake.png"
    fake.write_bytes(b"not an image")
    with pytest.raises(ScrubError, match="signature"):
        scan(fake, "privacy")

    truncated = tmp_path / "truncated.png"
    _make_image(truncated, "png")
    truncated.write_bytes(truncated.read_bytes()[:24])
    with pytest.raises(ScrubError, match="validation reported warnings"):
        scan(truncated, "privacy")


def test_clean_preserves_icc_profile(tmp_path: Path) -> None:
    source = tmp_path / "profiled.png"
    output = tmp_path / "profiled_clean.png"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (3, 3), (40, 60, 80)).save(source, icc_profile=profile)
    _set_tags(source, "-XMP-dc:Creator=Alice")

    before = scan(source, "max")
    assert any(
        field.name.startswith(("ICC_Profile:", "ICC-header:", "ICC-chrm:"))
        for field in before
    )
    clean(source, output, "max")
    with Image.open(output) as cleaned:
        assert cleaned.info["icc_profile"] == profile


def test_ai_mode_removes_png_parameters_metadata(tmp_path: Path) -> None:
    source = tmp_path / "parameters.png"
    output = tmp_path / "parameters_clean.png"
    _make_image(source, "png")
    _set_tags(
        source,
        "-PNG:Parameters=Stable Diffusion prompt: soft watercolor landscape",
        "-PNG:AestheticScore=7.5",
    )

    assert any(
        field.name == "PNG:Parameters" and field.disposition == "removable"
        for field in scan(source, "ai")
    )
    assert any(
        field.name == "PNG:AestheticScore" and field.disposition == "removable"
        for field in scan(source, "ai")
    )
    max_fields = scan(source, "max")
    assert any(
        field.name == "PNG:Filter" and field.disposition == "preserved"
        for field in max_fields
    )
    clean(source, output, "ai")
    assert not any(field.name == "PNG:Parameters" for field in scan(output, "ai"))
    assert not any(field.name == "PNG:AestheticScore" for field in scan(output, "ai"))


def test_unknown_png_prompt_is_reported_unsupported(tmp_path: Path) -> None:
    source = tmp_path / "unknown_prompt.png"
    output = tmp_path / "unknown_prompt_clean.png"
    text = PngInfo()
    text.add_text("Prompt", "Stable Diffusion prompt")
    Image.new("RGB", (3, 3)).save(source, pnginfo=text)

    assert any(
        field.name == "PNG:Prompt" and field.disposition == "unsupported"
        for field in scan(source, "ai")
    )
    with pytest.raises(ScrubError, match="unwritable metadata"):
        clean(source, output, "ai")


def test_privacy_removes_generic_capture_dates(tmp_path: Path) -> None:
    source = tmp_path / "dated.jpg"
    output = tmp_path / "dated_clean.jpg"
    _make_image(source, "jpg")
    _set_tags(
        source,
        "-IFD0:ModifyDate=2024:01:02 03:04:05",
        "-EXIF:CreateDate=2024:01:02 03:04:05",
        "-EXIF:ExifImageWidth=11",
        "-EXIF:ExifImageHeight=7",
    )

    fields = scan(source, "privacy")
    dates = [
        field for field in fields if field.name.endswith(("ModifyDate", "CreateDate"))
    ]
    assert len(dates) == 2
    assert all(field.disposition == "removable" for field in dates)
    clean(source, output, "privacy")
    assert not [
        field
        for field in scan(output, "privacy")
        if field.name.endswith(("ModifyDate", "CreateDate"))
    ]


def test_unicode_paths_and_missing_exiftool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "画像-é"
    directory.mkdir()
    source = directory / "家族.jpg"
    output = directory / "家族_clean.jpg"
    exif = Image.Exif()
    exif[315] = "Alice"
    exif[305] = "Photo Editor"
    exif[531] = 1  # Required by ExifTool's JPEG validation rules.
    Image.new("RGB", (5, 4), (50, 90, 130)).save(source, exif=exif)

    clean(source, output, "privacy")
    assert not [
        field for field in scan(output, "privacy") if field.disposition == "removable"
    ]

    monkeypatch.setattr(image_handler.shutil, "which", lambda _: None)
    with pytest.raises(ScrubError, match="ExifTool is required"):
        scan(source, "privacy")


def test_c2pa_marker_in_file_group_is_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "marker.jpg"
    _make_image(source, "jpg")
    results = iter(
        [
            subprocess.CompletedProcess([], 0, '[{"ExifTool:Validate":"OK"}]', ""),
            subprocess.CompletedProcess(
                [], 0, '[{"File:C2PA":"manifest present"}]', ""
            ),
        ]
    )
    monkeypatch.setattr(
        image_handler, "_run_exiftool", lambda _exe, _args: next(results)
    )

    fields = scan(source, "privacy")
    assert any(
        field.name == "File:C2PA" and field.disposition == "unsupported"
        for field in fields
    )


def test_clean_never_overwrites_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source.webp"
    output = tmp_path / "out.webp"
    _make_image(source, "webp")
    output.write_bytes(b"keep me")

    with pytest.raises(ScrubError, match="already exists"):
        clean(source, output, "privacy")
    assert output.read_bytes() == b"keep me"


def test_detected_c2pa_provenance_blocks_rewrite(tmp_path: Path) -> None:
    source = tmp_path / "credentialed.jpg"
    output = tmp_path / "credentialed_clean.jpg"
    _make_image(source, "jpg")
    _set_tags(source, "-XMP-dc:Description=C2PA content credentials")

    assert any(field.disposition == "unsupported" for field in scan(source, "privacy"))
    with pytest.raises(ScrubError, match="unsupported or unwritable metadata"):
        clean(source, output, "privacy")
    assert not output.exists()


def test_privacy_removes_jpeg_gps_and_iptc_location(tmp_path):
    source = tmp_path / "location.jpg"
    output = tmp_path / "location_clean.jpg"
    _make_image(source, "jpg")
    _set_tags(
        source,
        "-GPSLatitude=12.34",
        "-GPSLatitudeRef=N",
        "-GPSLongitude=56.78",
        "-GPSLongitudeRef=E",
        "-IPTC:City=Example City",
    )

    expected = {"GPS:GPSLatitude", "GPS:GPSLongitude", "IPTC:City"}
    assert expected <= {
        field.name
        for field in scan(source, "privacy")
        if field.disposition == "removable"
    }
    clean(source, output, "privacy")
    assert not expected.intersection(field.name for field in scan(output, "privacy"))
