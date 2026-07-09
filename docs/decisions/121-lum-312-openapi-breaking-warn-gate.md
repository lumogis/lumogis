# ADR-121: OpenAPI breaking gate ERR → WARN burn-in (LUM-312)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-312](https://linear.app/lumogis/issue/LUM-312)

**Related:** [ADR-060](060-lum-302-openapi-breaking-change-classifier.md)

## Context

After LUM-302 rollout, CI defaulted `OPENAPI_BREAKING_FAIL_ON=ERR`. LUM-312 tightens to **WARN** after burn-in so potential-breaking findings also fail.

## Decision

- **`.github/scripts/openapi-breaking-check.sh`** — default / CI env uses WARN-level fail.
- **`.github/workflows/ci.yml`**, **CONTRIBUTING.md**, **docs/LUMOGIS_REFERENCE_MANUAL.md** — document WARN contract.
- **ADR-060** status history note: burn-in complete on private CI.

## Consequences

- **Easier:** Catches WARN-class breaking changes before they reach consumers.
- **Harder:** More PRs may need snapshot/breaking justification.

## Status history

- 2026-06-22: Finalised by `/record-retro` — on `dev` @ `c1d30c1d4`.
