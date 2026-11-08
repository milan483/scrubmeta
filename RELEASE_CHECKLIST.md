# v0.1.0 release checklist

The merged MVP supports offline scan, clean, and verify for DOCX, PDF, JPEG,
PNG, and WebP. The candidate version remains `0.1.0`. See the
[release preparation issue](https://github.com/milan483/scrubmeta/issues/3)
and its linked PR for the exact validation results.

## Candidate checks

- Build the sdist and wheel in an isolated environment with `python -m build`.
  Run `python -m twine check --strict dist/*`.
- Inspect the sdist for `README.md` and the package modules. In the wheel,
  inspect the modules and the README text in the distribution's `METADATA`.
- Install the wheel in a fresh virtual environment and test it from outside the
  source tree. Run `scrubmeta --help`, then scan, clean, and verify a synthetic
  file. Check that the original bytes stay intact.
- Confirm that the Ubuntu and Windows test matrix and the packaging job pass.
- **Release blocker**. The owner needs to choose a license, for example MIT or
  Apache-2.0. The repository and package currently carry no project license.
  Tagging, publishing a release, and uploading distributions require separate
  authorization.

## Manual application QA

Use synthetic fixtures with identifiable metadata and visible content. Compare
the original with its cleaned sibling and record the application version,
platform, mode, and result. Mark any application you did not run as untested.

| Application | Compare original and cleaned copy |
| --- | --- |
| Microsoft Word and LibreOffice Writer | Reopen both DOCX files. Check text, layout, page breaks, styles, and embedded images. Confirm the selected properties were removed. |
| PDF viewers, such as Acrobat Reader, Okular, or Poppler | Reopen or render all pages. Compare page count, visible text, fonts, and images. Check DocumentInfo and XMP with scrubmeta. |
| Image viewers or editors, such as Gwenview, Photos, GIMP, or ImageMagick | Reopen JPEG, PNG, and WebP. Use an asymmetric JPEG with EXIF rotation and a fixture with an ICC profile. Compare displayed orientation, color, dimensions, and pixel content. |

CI covers automated checks. Record interactive viewer and editor checks
separately, including applications and platforms that were unavailable.
