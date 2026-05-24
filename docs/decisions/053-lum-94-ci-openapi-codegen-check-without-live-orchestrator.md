# ADR-053: CI OpenAPI codegen check without live orchestrator (LUM-94)

**Status:** Finalised
**Created:** 2026-05-21
**Issue:** [LUM-94](https://linear.app/lumogis/issue/LUM-94/ci-openapi-codegen-check-without-live-orchestrator)
**Related:** ADR 037 (RC gates and `web-codegen-check` / `openapi-check` wording); cross-device web plan Pass 0.3 step 19 (mechanism shipped earlier)

## Context

Lumogis Web (`clients/lumogis-web/`) generates a typed API client from a committed snapshot of the orchestrator OpenAPI spec (`clients/lumogis-web/openapi.snapshot.json`) via `openapi-typescript`. Without a CI guard, a maintainer can add, rename, or remove a v1 route and ship the change without regenerating the snapshot — the SPA's typed client then silently lies about the contract. LUM-94 asks for a CI check that catches that drift **without** spinning up a live orchestrator + Postgres + Qdrant + Ollama stack, and for a documented `make` target so maintainers can run it locally.

The cross-device web plan already shipped `orchestrator/scripts/dump_openapi.py`, `orchestrator/tests/test_api_v1_openapi_snapshot.py`, and `clients/lumogis-web/scripts/codegen.mjs --check`. This ADR records the **discoverability and explicit GitHub status** layer: `make openapi-check` as an alias of `make web-codegen-check`, a path-gated **`openapi-check`** job in `.github/workflows/ci.yml` (LUM-254 / LUM-258 always-reporting pattern), and documentation corrections for offline vs live codegen.

## Decision

1. **Canonical mechanism:** Keep `dump_openapi.py` → `app.openapi()` with test-only env hatches (`_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION`, `RERANKER_BACKEND=none`), deterministic JSON normalisation, drift tests in pytest + `codegen.mjs --check`. **No** live HTTP server in the default check path.

2. **Discoverability:** `openapi-check` Makefile target is a **plain alias** for `web-codegen-check` (same recipe).

3. **CI:** Dedicated **`openapi-check`** workflow job runs alongside **`lint-and-test`** (does not replace pytest snapshot coverage). Path script `.github/scripts/openapi-check-paths.sh` gates `npm ci` + `make openapi-check`; skipped PRs emit `SKIP_OPENAPI_CHECK: no paths matched contract` and exit 0; `push` to `main`/`master` runs the check unconditionally (workflow-level branch filter).

4. **Documentation:** Contributor and reference docs describe **`codegen:check` / `make web-codegen-check` / `make openapi-check`** as offline `dump_openapi` vs committed snapshot; **live** pull remains **`npm run codegen -- --live`** with **`LUMOGIS_OPENAPI_URL`**.

5. **Rejected alternatives:** TestClient-only OpenAPI fetch, parallel `MockApp` for spec-only, and curl against a running orchestrator — same rationale as draft ADR (`.cursor/adrs/LUM-94-ci-openapi-codegen.md`).

## Consequences

- Per-PR GitHub status can surface **`openapi-check`** independently of **`lint-and-test`** when branch protection is configured (**LUM-273**).
- Maintainers get a memorable **`make openapi-check`** name aligned with the API contract story.
- Snapshot drift remains a hard requirement for route changes; `openapi.snapshot.json` may need refresh when FastAPI/OpenAPI emission changes even without route edits (CHANGELOG on this chunk records a schema delta refresh).

## Status history

- 2026-05-21: Draft created by `/explore --headless` LUM-94 — `.cursor/adrs/LUM-94-ci-openapi-codegen.md`
- 2026-05-21: Finalised by `/verify-plan --headless` — implementation matches Option 1; canonical copy at this path.
