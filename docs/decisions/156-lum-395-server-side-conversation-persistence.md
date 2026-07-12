# ADR-156: Server-side conversation persistence — LUM-162 delta gap-fill (LUM-395)

**Status:** Finalised
**Created:** 2026-07-06
**Last updated:** 2026-07-07
**Decided by:** /explore --headless; implemented and finalised by /verify-plan --headless (LUM-395)

## Context

LUM-395's Linear text described greenfield `web_conversations` schema and Alembic migration, but **LUM-162** (ADR 074) and **LUM-439** (ADR 085) already shipped the table, full `/api/v1/conversations` CRUD, transcript sync, purge guards, and 20+ unit-level isolation tests. The repo uses **numbered raw SQL migrations** (`001`–`046`); there is **no Alembic**.

Gaps blocking **LUM-205** (Web chat): citation/proposal fields on persisted messages, a live two-user Postgres isolation proof, and accurate client docstrings.

## Decision

Scope LUM-395 as an **additive gap-fill on the shipped LUM-162 surface**:

1. Migration **047** adds nullable **`source_refs` JSONB** and **`action_proposal_id BIGINT NULL REFERENCES action_proposals(id) ON DELETE SET NULL`** on `web_messages`.
2. Extend `ConversationMessage` / `ConversationMessageAppendRequest` with optional fields; thread through `append_web_message` and `get_conversation`.
3. Proposal ownership: bare parameterised `SELECT user_id FROM action_proposals WHERE id = %s` — unknown id → **422** `invalid_action_proposal`; cross-user → **404** `conversation_not_found` (no leak). No import of `services.proposal_queue`.
4. `get_conversation` message fetch catches **`psycopg2.errors.UndefinedTable` only** (pre-027); missing-047 column errors propagate (fail loud).
5. Live integration test **`test_two_user_conversation_isolation_live.py`**: real Postgres + JWT `TestClient` + `create_user` (novel harness combo).
6. `threadStore.ts` header comment corrected (no reducer change).

Rich `ActionProposal` payload shape remains **LUM-100**; full server-side thread load remains **LUM-205**.

## Alternatives Considered

- **Greenfield re-spec (Alembic, UUID FKs, rename columns):** rejected — duplicates Done LUM-162 work.
- **JSONB snapshot column for proposals:** rejected — FK link chosen for lifecycle join.
- **Test/docs-only without columns:** rejected — LUM-205 needs stable persisted-message contract now.

## Consequences

- **Easier:** LUM-205 can rely on `source_refs` and `action_proposal_id` on `ConversationMessage`; existing LUM-162 tests remain valid (additive API).
- **Harder / constrained:** Proposal lifecycle stays in `action_proposals`; citations stored as opaque JSONB (XSS-safe rendering is client-owned).
- **Downstream:** Linear LUM-395 acceptance should be rewritten from greenfield/Alembic to as-built delta via `/linear-update`.

## Revisit conditions

- LUM-100 may require a different message↔proposal linkage than FK id.
- Independent citation querying may warrant a `message_citations` table.
- Migration filename collision hygiene (`024`/`043`/`044` duplicates) remains orthogonal tech-debt.

## Status history

- 2026-07-06: Draft created by /explore --headless (LUM-395).
- 2026-07-07: Finalised by /verify-plan --headless — implementation confirmed.
- 2026-07-10: LUM-589 — deferred `openapi-breaking-check` executed (oasdiff, `--fail-on WARN`) against the pre-LUM-395 snapshot (`fdb55a8^`). Result: **6 changes, 0 error / 0 warning / 6 info — no breaking changes**. The new `source_refs` and `action_proposal_id` fields are classified **non-breaking**: `response-optional-property-added` (×4 on `GET`/`POST /api/v1/conversations/{id}` message responses) and `new-optional-request-property` (×2 on the message-append request). Both the project gate (`.github/scripts/openapi-breaking-check.sh`) and the current-branch snapshot diff exit 0. Closes the LUM-395 DoD OpenAPI classification gap.
