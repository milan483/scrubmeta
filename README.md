# scrubmeta

scrubmeta scans, removes, and verifies selected embedded metadata in DOCX, PDF,
JPEG, PNG, and WebP files. It runs offline on Linux and Windows with Python 3.11
or later. Cleaning writes a sibling copy and leaves the original file intact.

## Install

Clone the repository and run the installation commands from its root. ExifTool
is required for images. Verify that `exiftool -ver` works in the terminal you use
to run scrubmeta.

On Debian or Ubuntu:

```sh
sudo apt install libimage-exiftool-perl
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/scrubmeta scan "My document.docx"
```

On Windows with PowerShell and Chocolatey:

```powershell
choco install exiftool
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\scrubmeta.exe scan "C:\Photos\Family photo.jpg"
```

You can also install the [official ExifTool Windows distribution](https://exiftool.org/)
and add its executable directory to PATH. Use the corresponding virtual
environment's Python for `python -m scrubmeta`.

## Use

```text
scrubmeta scan "Family photo.jpg"
scrubmeta scan "Family photo.jpg" --mode ai --show-values --json
scrubmeta clean "Family photo.jpg" --mode privacy
scrubmeta verify "Family photo_clean.jpg" --mode privacy --json
```

Paths can contain spaces and Unicode. The cleaner publishes a verified
`<stem>_clean<suffix>` copy. It stops if the output path already exists or
the destination filesystem cannot create a no-overwrite hard link. Move an
existing copy aside to rerun the original command. On Linux, the new copy
is readable and writable by its owner only.

| Mode | Behavior |
| --- | --- |
| `ai` | Targets recognized generation metadata, such as model, prompt, workflow, or generation software markers. Uses a bounded set of fields and markers. |
| `privacy` | Default. Targets supported author, software, date, location, description, and provenance metadata while preserving fields needed by the format. |
| `max` | Targets a broader set of supported nonessential fields where available. Review the copy before using it. |

Scan classifies fields as removable, preserved, or unsupported for the chosen
mode. Values are hidden unless you explicitly request `scan --show-values`.
Use `--json` on any command for machine-readable output.

| Exit code | Meaning |
| --- | --- |
| 0 | Scan or clean completed, or verify found no remaining targeted fields or detected unsupported fields. Check the status of a successful clean for unsupported fields. |
| 1 | Verify found remaining targeted fields. |
| 2 | Usage, file, dependency, or safety error. |
| 3 | Verify found detected unsupported or unknown metadata, without remaining known targets. |

## Coverage and limits

| Format | Embedded surfaces |
| --- | --- |
| DOCX | OOXML core, extended, and custom properties. Other document parts, relationships, styles, and media stay in the package. |
| PDF | DocumentInfo and supported document-level XMP properties. Pages and their content remain in the file. |
| JPEG, PNG, WebP | Supported EXIF, XMP, IPTC, and text tags, depending on the format and ExifTool write support. Image data is kept without re-encoding. |

Cleaning can refuse files with detected signatures, encryption, special-purpose
profiles, malformed structure, or content credentials where modification could
invalidate them. ExifTool validation warnings also block image cleaning.
Detection and readability checks cover specific structures.
They cannot certify PDF/A conformance, recognize every kind of signature,
detect every provenance marker, or replace rendering in a document viewer.

A `clean` verification result applies only to the supported metadata checks.
Undetected provenance can remain, including embedded attachments, document text,
pixels, invisible watermarks, and metadata in unsupported containers or
namespaces. scrubmeta provides no guarantee of anonymity or AI-detector results.
It leaves filesystem timestamps and ACLs outside the scope of sanitization.
