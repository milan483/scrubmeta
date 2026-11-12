"""Command-line entry point for scrubmeta."""

import argparse
import json
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from . import MODES, clean, scan, verify
from .common import ScrubError

_SCOPE = (
    "Verification covers supported embedded metadata only. Other provenance may remain."
)
_MAX = (
    "Max mode removes a broader set of supported metadata. Review the resulting copy."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scrubmeta",
        description="Inspect and remove selected embedded metadata from a copy.",
    )
    parser.add_argument("--version", action="version", version=version("scrubmeta"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("scan", "list embedded metadata fields"),
        ("clean", "write a sanitized sibling copy"),
        ("verify", "check readability and remaining metadata"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("path", help="input file")
        command.add_argument("--mode", choices=MODES, default="privacy")
        command.add_argument("--json", action="store_true", help="emit a JSON result")
        command.add_argument(
            "--recursive", action="store_true", help="include subdirectories"
        )
        if name == "clean":
            command.add_argument(
                "--output-dir", type=Path, help="directory for cleaned files"
            )
        if name == "scan":
            command.add_argument(
                "--show-values",
                action="store_true",
                help="include potentially sensitive values",
            )
    args = parser.parse_args(argv)

    report = {"mode": args.mode, "warnings": [_SCOPE]}
    if args.mode == "max":
        report["warnings"].append(_MAX)
    code = 0
    try:
        if Path(args.path).is_dir():
            from .batch import run_batch

            batch, code = run_batch(
                Path(args.path),
                args.command,
                args.mode,
                recursive=args.recursive,
                output_dir=getattr(args, "output_dir", None),
                show_values=getattr(args, "show_values", False),
            )
            report.update(batch)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                for result in report["results"]:
                    detail = f" ({result['error']})" if "error" in result else ""
                    print(f"{result['path']!r}: {result['status']}{detail}")
                    if getattr(args, "show_values", False):
                        for field in result.get("fields", []):
                            print(
                                f"  {field['disposition']}: {field['name']!r}"
                                f" = {field.get('value')!r}"
                            )
                print(
                    " ".join(
                        f"{key}={value}" for key, value in report["summary"].items()
                    )
                )
                for warning in report["warnings"]:
                    print(warning)
            return code
        if args.recursive or getattr(args, "output_dir", None) is not None:
            raise ScrubError("--recursive and --output-dir require a directory input.")
        if args.command == "scan":
            fields = scan(args.path, args.mode)
            report.update(status="scanned", readable=True)
        else:
            if args.command == "clean":
                output, result = clean(args.path, args.mode)
                report["output"] = str(output)
            else:
                result = verify(args.path, args.mode)
                code = {"clean": 0, "remaining": 1, "unsupported/unknown": 3}[
                    result.status
                ]
            report.update(status=result.status, readable=result.readable)
            fields = result.fields
        report["fields"] = [
            asdict(field)
            if getattr(args, "show_values", False)
            else {"name": field.name, "disposition": field.disposition}
            for field in fields
        ]
    except (ScrubError, OSError) as exc:
        report.update(status="error", readable=None, error=str(exc))
        code = 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(report["status"])
        if "error" in report:
            print(report["error"])
        if "output" in report:
            print(f"Output: {report['output']!r}")
        if report["readable"]:
            print("Readability check passed.")
        for field in report.get("fields", []):
            value = f" = {field['value']!r}" if field.get("value") is not None else ""
            print(f"  {field['disposition']}: {field['name']!r}{value}")
        for warning in report["warnings"]:
            print(warning)
    return code
