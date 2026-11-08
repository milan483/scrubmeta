# Repository instructions for Codex

## Project
`scrubmeta` is an offline, cross-platform Python CLI for **embedded file metadata** scanning, sanitization, and verification. It does not promise to remove all AI-detection evidence, embedded watermarks, or provenance signals.

## Where to start
- Read [codex_start_here.md](codex_start_here.md) and the linked GitHub issue before implementing.
- **GitHub issues are the source of truth for task-specific requirements and acceptance criteria.** Keep this file focused on durable project rules; do not duplicate issue specifications here.
- Work on a feature branch and propose changes through a draft PR. Do not commit implementation directly to `main` unless explicitly requested.

## Engineering rules
- Preserve all user/local/uncommitted changes; inspect status before editing, and never discard or overwrite unrelated work.
- Prefer a small, maintainable Python package and minimal dependencies. Use format-specific handlers; avoid GUI, network access, tracking, telemetry, or any external uploads.
- Non-destructive by default: never overwrite a source or destination silently. Preserve technical metadata required for appearance, orientation, color accuracy, document validity, and file structure.
- Treat unverified metadata surfaces as **unknown or unsupported**, not confirmed clean. Don't imply this tool defeats AI detectors or removes invisible watermarks.
- Avoid corrupting signed, encrypted, PDF/A, or other special-purpose documents; implement safe skips and document detection limits.
- Avoid using sensitive real documents for fixtures; generate synthetic test fixtures.
- Follow the behavior and CLI contract specified in the issue; when a design tradeoff is material, document it in the PR.

## Validation and delivery
- Add meaningful automated tests for every supported format and destructive/error edge cases.
- Run lint, unit/integration tests, and representative CLI smoke tests; maintain Ubuntu and Windows CI.
- Open a **draft PR**, link the issue, and post an **implementation + validation report as a PR conversation comment**. Include commands, actual results, limitations, and skipped checks. Keep the report factual.
- Do **not** merge PRs, enable auto-merge, publish releases, or delete branches without explicit user approval.
