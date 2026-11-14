# ADR-116: Lumogis Server deb build CI (LUM-492)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-492](https://linear.app/lumogis/issue/LUM-492)

**Related:** [ADR-100](100-lum-491-fused-hub-cleanup.md) (hub-build retired; Server CI follow-up)

## Context

LUM-491 removed the fused `hub-build.yml`. LUM-492 ships the replacement maintainer CI workflow for the Lumogis Server `.deb` prove path.

## Decision

Add **`.github/workflows/server-build.yml`** — path-gated on the appliance sources, its build targets and the bundled lock inputs; never runs on `lumogis/lumogis` (`github.repository != 'lumogis/lumogis'`). Prove job builds the Server deb on `ubuntu-22.04`. **`scripts/check-public-export.sh`** and **`orchestrator/tests/test_check_public_export_script.py`** enforce that the workflow is not part of the export.

## Consequences

- **Easier:** Server deb regressions caught on PR/push to private `main`/`dev`; export boundary documented in tests.
- **Harder:** Requires `CLOUDFLARE_API_TOKEN` / build secrets only on private CI; mac/win packaging remains LUM-468/LUM-474.

## Revisit conditions

- Promote mac/win matrix from LUM-468 spike into PR gate when per-OS venv + packaging proven.

## Status history

- 2026-06-22: Finalised by `/record-retro` — cherry-picked from stacked branch onto `dev` @ `c1d30c1d4`.
- 2026-11-10: public-boundary wording (LUM-898).
