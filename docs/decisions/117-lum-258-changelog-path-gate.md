# ADR-117: Changelog job-level path gate + always-reporting (LUM-258)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-258](https://linear.app/lumogis/issue/LUM-258)

**Related:** [ADR-045](045-changelog-ci-enforcement.md) (LUM-193 baseline)

## Context

ADR-045 shipped workflow-level changelog enforcement. LUM-258 adds job-level path filtering and always-reporting so required-check topology matches LUM-254/LUM-402 patterns.

## Decision

- **`.github/scripts/changelog-paths.sh`** — path gate contract (shared with local `make changelog-check` semantics).
- **`.github/workflows/changelog.yml`** — job-level paths + always-reporting required check posture.
- **CONTRIBUTING.md** — documents path gate behaviour.

## Consequences

- **Easier:** Docs-only PRs no longer pay changelog workflow cost incorrectly; required checks report consistently.
- **Harder:** Path list must stay synced with `scripts/changelog-gate-paths.txt` / Makefile.

## Status history

- 2026-06-22: Finalised by `/record-retro` — on `dev` @ `c1d30c1d4`.
