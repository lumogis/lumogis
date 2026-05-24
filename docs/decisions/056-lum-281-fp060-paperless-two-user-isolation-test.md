# ADR 056: LUM-281 / FP-060 — paperless two-user Postgres isolation test (as-shipped)

**Status:** Finalised
**Created:** 2026-05-21
**Last updated:** 2026-05-21
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-21 (Composer)
**Plan:** none — P1 follow-up from **`LUM-281`** verify; shipped as **LUM-304**
**Exploration:** `.cursor/explorations/lum_281_fp060_paperless_two_user_isolation_retro.md`
**Draft mirror:** `.cursor/adrs/lum_281_fp060_paperless_two_user_isolation.md`
**Linear:** [LUM-304](https://linear.app/lumogis/issue/LUM-304/p1-add-two-user-postgres-isolation-coverage-for-paperless-in-test-two) (parent [LUM-281](https://linear.app/lumogis/issue/LUM-281/feature-paperless-ngx-lumogis-ingest-v01-docker-hn-persona-a))

## Context

**LUM-281** verify (**ADR 054**) left a **P1** gap: plan §Integration required a **two-user Postgres-backed** extension to **`orchestrator/tests/integration/test_two_user_isolation.py`** for the paperless poll/ingest path. Portfolio row **FP-060** captured that gap; Linear child **LUM-304** was created post-verify and closed after the test landed on **`dev`** at **`189126a6b`**.

## Decision

Close **FP-060** with the as-shipped integration test **`test_two_users_have_independent_paperless_ingest`**, which:

1. Extends **`_IsolationStore`** to model **`sources`** and **`external_documents`** for per-user paperless configuration.
2. Exercises credential PUT routes, **`load_connection`**, **`_poll_paperless_source`**, and ingest side effects with a mocked **`PaperlessPoller`** (same external document id on both users, distinct content).
3. Asserts **`external_documents.user_id`**, logical paths, and **`external_document_chunk_point_id`** / Qdrant chunk payloads never cross user boundaries.

This is the canonical multi-user proof for paperless v0.1 alongside unit tests in **`test_paperless_connector.py`**.

## Alternatives considered

- **Unit-only isolation** (`test_paperless_connector.py`) — insufficient vs plan §Integration; rejected at verify.
- **Live paperless-ngx compose fixture** — deferred; mock adapter boundary chosen for CI cost (same as plan testing table posture).

## Consequences

**Positive:** **LUM-281** verify P1 can be treated as resolved; **ADR 054** follow-up bullet on missing integration row is superseded by this record.

**Limits:** No live paperless image; scheduler loop not fully integration-tested.

## Revisit conditions

- Schema drift on **`external_documents`** / **`sources`** without updating **`_IsolationStore`** — reopen test maintenance.
- Production incident suggesting poll-path isolation bug despite green test — add live fixture or broaden **`feed_monitor.start()`** coverage.

## Linear linkage (Product OS)

- **LUM-304:** Done (child — implements this ADR scope)
- **LUM-281:** Done (parent — ingest connector; P1 cleared by this child)
- **New issue needed:** no

## Testing retrospective

See exploration **`lum_281_fp060_paperless_two_user_isolation_retro.md`**. Full orchestrator suite was green at **LUM-281** merge (**1783** passed); targeted paperless integration row not re-run in retro pass due to **`AUTH_SECRET`** placeholder gate in local compose.

## Status history

- **2026-05-21:** Finalised by **`/record-retro`** FP-060 — product commit **`189126a6b`** on **`dev`**.
