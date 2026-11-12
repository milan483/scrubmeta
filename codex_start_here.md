# Codex: start here

**Repository** [milan483/scrubmeta](https://github.com/milan483/scrubmeta)

## Project status

- [Issue #1](https://github.com/milan483/scrubmeta/issues/1), the offline metadata CLI MVP, was merged through [PR #2](https://github.com/milan483/scrubmeta/pull/2).
- [Issue #3](https://github.com/milan483/scrubmeta/issues/3) tracks release packaging and installation preparation (draft PR #5). The [release checklist](RELEASE_CHECKLIST.md) covers artifact validation, application QA, and the MIT license.
- [Issue #4](https://github.com/milan483/scrubmeta/issues/4), safe recursive batch processing, is implemented on the 1.0.0 task branch for review.

## Workflow

1. Read [AGENTS.md](AGENTS.md) and the issue assigned for the current cycle. Confirm the branch and working tree before editing, and preserve local changes.
2. Implement on a feature branch based on the relevant `main` baseline. Run the validation required by the assigned issue.
3. Open or update the draft PR linked to the issue and post a separate validation comment with commands, results, and skipped checks.
4. Leave merging, licensing, tags, releases, and package publication to separately authorized work.

## 1.0.0 acceptance

Validate the single-file and directory workflows, safety/error regressions, all five
formats, installed distributions, and Ubuntu/Windows CI before recommending the
1.0.0 branch for merge. Record release approval separately.
