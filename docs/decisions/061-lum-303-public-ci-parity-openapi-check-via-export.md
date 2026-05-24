# ADR-061: Public `lumogis/lumogis` CI parity for `openapi-check` via export presence (LUM-303)

**Status:** Finalised

**Created:** 2026-05-23

**Last updated:** 2026-05-23

**Decided by:** `/explore --headless` LUM-303 + `/review-plan --arbitrate` R1; finalised by `/verify-plan --headless` 2026-05-23

**Issue:** [LUM-303](https://linear.app/lumogis/issue/LUM-303/p3-public-lumogislumogis-ci-parity-for-openapi-check-job)

**Related:** [ADR 037](037-ghcr-publish-public-repo-only.md) (GHCR publish public-repo-only), [ADR 053-lum-94](053-lum-94-ci-openapi-codegen-check-without-live-orchestrator.md) (offline OpenAPI gate), [ADR 060-lum-302](060-lum-302-openapi-breaking-change-classifier.md) (breaking classifier — coordinates with public export surface).

## Context

LUM-94 shipped a path-gated **`openapi-check`** job in **`.github/workflows/ci.yml`** that runs offline snapshot/codegen checks. ADR 037 establishes that the public AGPL tree is an **export** of private work, not a second CI source. The remaining risk was **implicit** parity: a strip-list or packaging mistake could drop workflow helpers, fixtures, or the job definition from the export while leaving publish gates green until someone noticed missing coverage on **`lumogis/lumogis`**.

## Decision

1. **Enforce a canonical 12-path filesystem contract** on every candidate public tree at the end of **`scripts/check-public-export.sh`**, before the success line: assert each path exists under the export root and that **`.github/workflows/ci.yml`** contains a top-level job line matching **`^  openapi-check:[[:space:]]*$`** (two-space indent under **`jobs:`**, as in the private workflow today). Violations **`die`** with stable substrings **`openapi-ci-export-contract`** and **`LUM-303`** on stderr and in the **`die`** message so pytest can assert combined output.

2. **Defensive strip-list intersection:** if **`scripts/public-export-strip-list.txt`** lists any of the same canonical paths as a non-comment line, **`die`** — today the intersection is empty; the guard prevents future accidental removal of OpenAPI CI inputs from the export.

3. **Document** maintainer expectations in **`CONTRIBUTING.md`** (“Public CI parity (OpenAPI)”) — do not add canonical paths to the strip list without updating the script comment block, pytest tuple, and docs in the same change.

4. **Non-goal:** this gate does **not** run **`make openapi-check`** on the export checkout; deeper offline execution on the export tree remains **LUM-313** (and related follow-ups).

5. **Do not** add a separate public-only OpenAPI workflow file — rejected as dual source of truth (see draft exploration in **`.cursor/explorations/archived/`** after merge-workflow archive).

## Alternatives Considered

- **Separate public-only `openapi-check.yml`** — Rejected (maintenance burden; contradicts ADR 037’s single-source story for exported workflows).

- **Documented skip + manual sign-off only** — Rejected; loses free contributor PR drift coverage on the public repo.

## Consequences

- **`make verify-public-rc`** / **`verify-public-rc-full`** always run **`scripts/check-public-export.sh`** after **`create-upstream-export-tree.sh`**; the new block runs in both flows whenever the export gate is reached.

- Renaming or splitting the **`openapi-check`** job id requires updating the assertion and **`orchestrator/tests/test_check_public_export_script.py`** in the same change as the workflow (**LUM-258** topology work may force the same).

- **Presence ≠ runnable proof** on the export tree — still explicitly deferred to **LUM-313**.

## Status history

- 2026-05-23: Draft created by `/explore --headless` LUM-303 (`.cursor/adrs/LUM-303-public-ci-parity-openapi-check.md`).
- 2026-05-23: Revised during `/review-plan --arbitrate` R1 (12-path contract, grep safety, strip-list tests).
- 2026-05-23: Finalised by `/verify-plan --headless` — implementation confirmed; canonical copy this file.
