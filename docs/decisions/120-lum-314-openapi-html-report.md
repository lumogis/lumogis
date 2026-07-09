# ADR-120: Optional OpenAPI changes HTML report (LUM-314)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-314](https://linear.app/lumogis/issue/LUM-314)

**Related:** [ADR-060](060-lum-302-openapi-breaking-change-classifier.md)

## Context

P3 follow-up: human-readable OpenAPI drift artefact for reviewers when snapshot changes.

## Decision

**`.github/workflows/ci.yml`** — optional HTML report step on openapi-check job path; artefact upload for drift review. **CONTRIBUTING.md** notes how to download/interpret.

## Consequences

- **Easier:** Reviewers see structured diff without local oasdiff runs.
- **Harder:** Slightly more CI artefact storage.

## Status history

- 2026-06-22: Finalised by `/record-retro` — on `dev` @ `c1d30c1d4`.
