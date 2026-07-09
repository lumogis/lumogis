# ADR-152: Default-user remap table coverage on auth flip — LUM-473 follow-up (as-shipped)

**Status:** Finalised

**Created:** 2026-07-05

**Last updated:** 2026-07-05

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-07-05 (Composer)

**Plan:** none — shipped before formal plan / verify cycle for this slice

**Exploration:** `.cursor/explorations/lum_473_default_user_remap_coverage_retro.md`

**Draft mirror:** `.cursor/adrs/lum_473_default_user_remap_coverage.md`

**Extends:** [ADR-142](142-lum-473-native-core-auth-loopback-chunk-a.md) (LUM-473 Chunk A)

**Linear:** [LUM-473](https://linear.app/lumogis/issue/LUM-473) — follow-up under parent programme (Chunk B still open)

## Context

LUM-473 Chunk A ([ADR-142](142-lum-473-native-core-auth-loopback-chunk-a.md)) enabled opt-in household sharing on the Lumogis Server appliance, flipping Core to `AUTH_ENABLED=true` on loopback. Boot invokes `orchestrator/db_default_user_remap.py` to re-attribute legacy `user_id='default'` rows to the bootstrap admin so pre-multi-user content remains visible under the post-013 scope model.

After Chunk A merged, `_SCOPED_TABLES` still omitted **19** user-scoped tables added in later migrations (memories, entity_edges, web chat, captures, notifications, MCP tokens, auth sessions, purge tombstones, etc.). After auth flip, those rows stayed stranded and invisible to the bootstrap admin. A second defect allowed `INBOX_OWNER_USER_ID=default` to win when `AUTH_ENABLED=true`, silently no-oping the remap — the same failure mode identified during Chunk A plan review but not fully closed in the allowlist.

Cursor branch `critical-bug-investigation-c937` fixed both issues and merged to `dev` at `61ad663fb` (2026-07-05).

## Decision

**Keep `db_default_user_remap._SCOPED_TABLES` exhaustive against the Postgres schema and reject `INBOX_OWNER_USER_ID=default` when auth is enabled.**

Concrete as-built surface:

- **`_SCOPED_TABLES`:** full coverage of every table with a `user_id` column in `postgres/init.sql` and `postgres/migrations/`, except `users` (identity table — listed in `_REMAP_INTENTIONAL_EXCLUSIONS`).
- **Resolver guard:** if `INBOX_OWNER_USER_ID` is the literal `default` and `AUTH_ENABLED=true`, log a warning and fall through to `LUMOGIS_BOOTSTRAP_ADMIN_EMAIL` resolution instead of returning `default`.
- **Regression test:** `orchestrator/tests/test_default_user_remap_tables_exhaustive.py` — SQL discovery + stale-entry check + export-table ⊆ remap-table invariant (mirrors `test_user_export_tables_exhaustive`).

**Explicit non-goals:** No remap semantics change beyond the guard; no new migration; no Chunk B LAN work.

## Alternatives considered

- **Manual allowlist updates only when bugs reported:** rejected — silent data loss on auth flip is unacceptable; exhaustive test prevents recurrence.
- **Drop `_SCOPED_TABLES` in favour of dynamic `information_schema` scan at runtime:** not chosen — explicit allowlist documents intent and avoids remapping tables that carry `user_id` for non-ownership reasons without review.
- **Hard-fail boot on `INBOX_OWNER_USER_ID=default` when auth on:** not chosen — fall-through to bootstrap email preserves operator recovery path.

## Consequences

**Easier:**

- Auth flip on Server and Docker no longer strands legacy content in memories, web conversations, captures, or notification prefs.
- CI catches missing allowlist entries when migrations add user-scoped tables.

**Harder:**

- Three allowlists (`_SCOPED_TABLES`, `_USER_EXPORT_TABLES`, permissions tests) must stay aligned when schema evolves.

**Future chunks must know:**

- LUM-473 Chunk B (LAN/Caddy/DNS-01) unchanged — still blocked by LUM-508.
- New `user_id` tables → update `_SCOPED_TABLES` in the same PR or exhaustive test fails.

## Revisit conditions

- Exhaustive test reports missing tables → add to `_SCOPED_TABLES` or justify exclusion.
- If runtime dynamic discovery is adopted later → replace allowlist + update test strategy in a dedicated ADR.

## Linear linkage (Product OS)

- **LUM-473** — recommend **`/linear-update comment LUM-473`** with merge SHA `578eb65ef`, ADR-152 link, 8/8 remap tests green. Parent issue remains open until Chunk B closes programme scope.

## Testing retrospective

| Item | Detail |
| --- | --- |
| Tests added/changed | `test_default_user_remap_tables_exhaustive.py` (new); `test_default_user_remap_target.py` (guard behaviour) |
| Commands | Docker compose orchestrator pytest on the two test modules (see exploration) |
| Results | **8 passed**, 0 failed |
| Gaps | No end-to-end auth-flip integration across all table classes |
| Follow-ups | none blocking |
| Docs | `automated-test-strategy.md` — no change |

## Status history

- 2026-07-05: Finalised by /record-retro (merged to `dev` at `61ad663fb`).
