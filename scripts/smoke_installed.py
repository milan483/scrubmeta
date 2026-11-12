"""Exercise an installed wheel from a temporary directory outside the checkout."""

import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

import pikepdf

import scrubmeta


def invoke(*arguments: str, expected: int = 0) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "scrubmeta", *arguments, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def main() -> None:
    installation = Path(scrubmeta.__file__).resolve()
    assert installation.is_relative_to(Path(sys.prefix).resolve()), installation
    result = subprocess.run(
        [sys.executable, "-m", "scrubmeta", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == version("scrubmeta")
    with TemporaryDirectory(prefix="scrubmeta-installed-") as directory:
        root = Path(directory)
        source = root / "資料"
        (source / "nested").mkdir(parents=True)
        path = source / "nested" / "fixture.pdf"
        with pikepdf.new() as document:
            document.add_blank_page()
            document.docinfo["/Author"] = "Synthetic author"
            document.save(path)
        original = path.read_bytes()
        assert invoke("scan", str(path))["status"] == "scanned"
        assert invoke("verify", str(path), expected=1)["status"] == "remaining"
        assert invoke("clean", str(path))["status"] == "clean"
        assert (
            invoke("verify", str(path.with_stem("fixture_clean")))["status"] == "clean"
        )
        path.with_stem("fixture_clean").unlink()
        batch = invoke("clean", str(source), "--recursive")
        assert batch["summary"]["cleaned"] == 1
        output = root / "資料_clean"
        assert (output / "nested" / "fixture.pdf").is_file()
        assert invoke("verify", str(output), "--recursive")["summary"]["verified"] == 1
        assert (
            invoke("clean", str(source), "--recursive", expected=2)["summary"]["errors"]
            == 1
        )
        assert path.read_bytes() == original
    print(
        f"Installed scrubmeta {version('scrubmeta')} single-file and directory smoke passed"
    )


if __name__ == "__main__":
    main()
