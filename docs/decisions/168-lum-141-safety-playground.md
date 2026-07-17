# ADR 168: Safety playground — live injection test suite (LUM-141)

**Status:** Finalised
**Created:** 2026-07-14
**Last updated:** 2026-07-14
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-14 (Composer)
**Plan:** none — shipped on `claude/lum-141-safety-playground` before formal plan / verify cycle
**Exploration:** `.cursor/explorations/safety_playground_retro.md`
**Draft mirror:** `.cursor/adrs/safety_playground.md`
**Linear:** [LUM-141](https://linear.app/lumogis/issue/LUM-141/safety-playground-admin-injection-test-suite-live-sanitiser-validation)

## Context

LUM-127 (ADR-039) and follow-on scanners (ADR-166, LUM-361/362) added defensive primitives across ingest, retrieval, tool results, user config, and action policy — but Lumogis had no operator-facing way to throw known-bad payloads at the **live** rules and see pass/fail. LUM-141 requested an admin-only playground with a curated injection suite and CI gate.

Branch `claude/lum-141-safety-playground` merged to `dev` at `4cba6704c` (2026-07-14).

## Decision

Ship an admin-only **safety playground** that runs a static adversarial suite against pure detection primitives:

### Invariants

- Calls only: `sanitise_at_ingest`, `wrap_retrieved_chunk`, `scan_tool_result`, `PatternSecretsScanner.scan`, `is_hard_limited`.
- **Never** calls side-effecting middleware (`guard_tool_result`, `check_permission`).
- Suite runs are dry-run: no persistence, hooks, LLM, or audit writes.

### Surfaces

| Surface | Path / env |
| --- | --- |
| Service | `orchestrator/services/safety_playground.py` — `INJECTION_TEST_CASES` (24 cases, 5 vectors) |
| Routes | `orchestrator/routes/admin_safety.py` — `/api/v1/admin/safety/{cases,run,probe}` |
| Enable gate | `SAFETY_PLAYGROUND_ENABLED` (default `true`; `404` when off) |
| Auth | `require_admin`; `require_same_origin` on POST |
| Web UI | `AdminSafetyPlaygroundView` under admin nav — run suite + ad-hoc probe |
| CI gate | `orchestrator/tests/test_injection_suite.py::test_no_hard_failures_against_live_defences` |

### Vectors and expectations

1. **document_ingest** → `sanitise_at_ingest` (FLAGGED/BLOCKED/PASSED)
2. **session_context** → `wrap_retrieved_chunk` origin envelope (ORIGIN_TAGGED)
3. **tool_result** → `scan_tool_result` (BLOCKED)
4. **user_config** → secrets scanner (BLOCKED; never logs matched secret substrings)
5. **action_execution** → `is_hard_limited` (BLOCKED vs PASSED control)

### Known gaps (`known_gap=True`)

Three document_ingest cases document real sanitiser gaps (zero-width obfuscation, HTML comments, base64-encoded instructions). They appear as **warnings** in the UI and `xfail(strict=True)` in CI — when defences land, xfail forces reclassification.

### Scanner-disabled deployments

When `SECRETS_SCANNER_ENABLED` or tool-result scanner gates are off, suite detail appends a note that the deployment does not enforce that scanner — avoiding false assurance.

## Alternatives considered

- **Mock sanitiser in tests only** — rejected; playground must hit live YAML/rules (Linear requirement).
- **User-facing playground** — rejected; admin-only per threat model.
- **Dynamic admin-editable suite** — deferred; static repo list enables deterministic CI.
- **Middleware wrappers in suite** — rejected; would write audit/action_log during “tests”.

## Consequences

- Regressions in LUM-127/361/362 primitives red CI via `test_injection_suite.py`.
- Sanitiser gap work (LUM-127) has concrete failing cases to close.
- Upload-test-document and session-replay flows from the Linear issue body are **not** in v1 — probe + static suite only.

## Revisit conditions

- Close each `known_gap` when LUM-127 (or successor) ships the matching rule/normaliser.
- Add upload/replay vectors when ingest/session E2E harness exists.
- Add Playwright admin E2E when admin auth CI is stable.

## Linear linkage (Product OS)

- **LUM-141** — primary issue; partial vs full Linear DoD (upload/replay deferred).
- **Follow-ups:** sanitiser gaps → **covered-by: LUM-127**; remaining Linear acceptance → `/linear-update apply-closure LUM-141` with scope note.

## Testing retrospective

- `pytest` safety + injection suite: **22 passed, 3 xfailed** (known gaps).
- Vitest `AdminSafetyPlaygroundView.test.tsx`: **1 passed**.
- Full `make test` not run in retro session — run before release promotion.

## Status history

- 2026-07-14: Finalised by `/record-retro` (retrospective) after merge `4cba6704c` on `dev`.
