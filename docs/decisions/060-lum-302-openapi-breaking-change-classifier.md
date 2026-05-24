# ADR-060: OpenAPI breaking-change classifier on snapshot drift (LUM-302)

**Status:** Finalised

**Created:** 2026-05-22

**Last updated:** 2026-05-22
**Decided by:** /explore (headless) + /review-plan --arbitrate R1; finalised by `/verify-plan --headless` 2026-05-22

## Context

LUM-94 (ADR **053-lum-94**) shipped an offline OpenAPI snapshot drift gate that fails CI on any path-set difference between the live `app.openapi()` (via `orchestrator/scripts/dump_openapi.py`) and the committed `clients/lumogis-web/openapi.snapshot.json`. The gate is intentionally binary: any drift fails, but contributors get no signal about *what kind* of change they made. LUM-302 (P2 follow-up from the 2026-05-21 verify of LUM-94) asks for a classifier on top of this gate that distinguishes breaking from non-breaking changes, integrated as a dedicated CI step, with a documented pass/fail contract in `CONTRIBUTING.md` and `docs/LUMOGIS_REFERENCE_MANUAL.md`.

Binding constraints inherited from ADR 053-lum-94:

- Offline — must not require a running orchestrator.
- AGPL-compatible licence (Apache-2.0 and MIT acceptable).
- No new Docker services for CI tooling unless justified.
- No new Python runtime dependencies in `orchestrator/requirements.txt` for a CI-only check.
- Must compose with the existing path-gated `openapi-check` job in `.github/workflows/ci.yml`, not replace it.

## Decision

Adopt **oasdiff** (Tufin, Apache-2.0, Go binary) as the OpenAPI breaking-change classifier. Wire it as a sibling step inside the existing path-gated `openapi-check` job, comparing the PR/HEAD `clients/lumogis-web/openapi.snapshot.json` against the same path at a **merge-base** of the PR head and base tips (or `HEAD~1` on push — see plan limits). Control the gate via a new `OPENAPI_BREAKING_FAIL_ON` CI env var (`ERR` | `WARN` | `INFO` | `off`), defaulting to **`ERR`** during initial rollout (oasdiff: `--fail-on ERR` exits non-zero **only** on definite breaking changes) and **tightening to `WARN`** after burn-in so potential-breaking WARN-level findings also fail CI (stricter than `ERR` — tracked as a follow-up Linear child). Document the resulting pass/fail contract in `CONTRIBUTING.md` and `docs/LUMOGIS_REFERENCE_MANUAL.md`. Public-repo (`lumogis/lumogis`) parity is handled in coordination with **LUM-303**.

**Implementation notes (as shipped):** Pinned **`github.com/oasdiff/oasdiff@v1.15.2`** with **`actions/setup-go@v5`** **`go-version: 1.26.x`** (module requires Go 1.26+). Shell orchestrator: **`.github/scripts/openapi-breaking-check.sh`**. Fixture smoke under **`scripts/fixtures/openapi-breaking-check/`** runs in CI before the repo snapshot compare.

## Alternatives Considered

- **pb33f/openapi-changes** — Strong human-facing HTML/markdown changelog; weaker as a primary gate because its breaking-change signal is binary (`--error-on-diff`) with per-rule overrides rather than oasdiff's ERR/WARN/INFO ladder. Reserved as an *optional* complementary artefact step in a later iteration.
- **teolzr/schema-diff** (Python) — Python-native but smaller rule set, newer, and licence unverified in initial research.
- **OpenAPITools/openapi-diff** (Java) — Capable but introduces a JRE toolchain to CI for capabilities oasdiff already exceeds.
- **Schemathesis** — Property-based fuzzer against a live server; wrong category for a static-spec classifier and violates the LUM-94 "no live orchestrator" constraint.
- **Custom in-repo classifier** — Reinvents oasdiff's 450+ rule library; rejected on cost.

See `.cursor/explorations/LUM-302-openapi-breaking-change-classifier.md` for the full comparison.

## Consequences

What becomes easier:

- Reviewers see *why* a snapshot diff matters (renamed field vs removed endpoint vs added optional property) directly in PR annotations, instead of only "snapshot drifted; regenerate".
- The existing strict snapshot gate (`test_snapshot_paths_match_live_spec`) can stay strict — the semantic signal moves into the new classifier step rather than being smuggled into the test.
- A graduated rollout (**`ERR` first** — fail only definite breaking changes — then **`WARN`** to also fail potential-breaking WARN-level findings) is possible because oasdiff supports severity-level gating natively (`--fail-on ERR` vs `--fail-on WARN` per upstream docs); no custom wrapper required.
- Contributor docs gain a clear "breaking-change contract" section, which feeds into future versioning / release-line decisions (Release & Export Hygiene theme).

What becomes harder:

- CI runners must have `oasdiff` available — installed via pinned `go install` using `setup-go` with module cache.
- The public-repo (`lumogis/lumogis`) CI must either mirror the new step or document the divergence; **LUM-303** scope expands accordingly.
- A `changes-rules.yaml` config and `--warn-ignore` / `--err-ignore` files become a small new maintenance surface.

What future chunks must know:

- The classifier composes *on top of* `dump_openapi.py` and the committed snapshot. Any change to the snapshot path, normalisation rules, or codegen pipeline must keep the classifier step in mind.
- The `OPENAPI_BREAKING_FAIL_ON` default is intentionally **`ERR`** initially (gentler gate); tightening to **`WARN`** is a deliberate, Linear-tracked follow-up, not an oversight.
- Schemathesis remains available for a separate *runtime conformance* exploration — this ADR rules it out only for the static-spec classification role.

## Revisit conditions

- If FastAPI starts emitting OpenAPI 3.2 (or beyond) and oasdiff lags 3.2 support, revisit the tool choice (3.1 support is currently beta in oasdiff).
- If oasdiff project activity stalls for >6 months or relicenses away from Apache-2.0, revisit (pb33f/openapi-changes becomes the natural fallback).
- If LUM-303 implementation reveals the public `lumogis/lumogis` workflow set cannot host a Go-binary step (e.g. minimal runner image), revisit installation strategy or consider the Python-native `teolzr/schema-diff` as a portability fallback.
- If a future runtime conformance exploration adopts Schemathesis or similar, this ADR does not change — it explicitly scopes to *static spec diff classification*.

## Status history

- 2026-05-22: Draft created by /explore (headless run for LUM-302).
- 2026-05-22: Revised during /review-plan --arbitrate R1 — corrected oasdiff `--fail-on` semantics: `--fail-on WARN` fails on **ERR and WARN**; `--fail-on ERR` fails **only** on ERR (gentler). Plan/ADR rollout is now **default `ERR`**, then tighten to **`WARN`** after burn-in (matches upstream docs).
- 2026-05-22: Finalised by `/verify-plan --headless` — implementation confirmed in `.github/scripts/openapi-breaking-check.sh`, `.github/workflows/ci.yml`, fixtures, `Makefile`, `CONTRIBUTING.md`, and `docs/LUMOGIS_REFERENCE_MANUAL.md`.
