# ADR: Per-user `file_index` and ingest attribution (audit B11 + B12)

**Status:** Finalised
**Created:** 2026-04-18
**Last updated:** 2026-04-19 (finalised by /verify-plan)
**Decided by:** composer-2 (Opus 4.7), via `/explore per_user_file_index_and_ingest_attribution`
**Finalised copy:** `docs/decisions/013-per-user-file-index-and-ingest-attribution.md`

## Context

The Family-LAN multi-user plan (*(maintainer-local only; not part of the tracked repository)*, ✅ implemented + hardened 2026-04-18) closed the *isolation* gap on chat / tool / search hot paths — the family-LAN floor proves Alice cannot read Bob's data. Two related defects survived that floor and were recorded as audit items **B11** and **B12** (`docs/private/MULTI-USER-AUDIT.md` §12 Phase B; restated in `docs/private/MULTI-USER-AUDIT-RESPONSE.md` lines 77–78 and §6 priority row #3):

- **B11** — `services/memory.py:94` and `services/ingest.py:180` derive Qdrant `point_id` deterministically from a user-shared key (`session::{session_id}` and `{file_path}::chunk-{i}`) with no `user_id` component.
- **B12** — `postgres/init.sql:10` declares `file_index.file_path` `UNIQUE NOT NULL`, so two users cannot both ingest the same absolute path; the second user's `INSERT` fails.

The two defects are **coupled**: fixing B12 alone (allowing two `(user_id, file_path)` rows) without fixing B11 turns today's loud `INSERT` failure into a silent Qdrant payload overwrite — user2's chunks would clobber user1's chunks under the same deterministic point id, then the `INSERT` would still fail, leaving the system in a corrupted state where Postgres attributes the file to user1 but Qdrant carries user2's text. The exploration's §"What's actually broken today" walks the trace step-by-step.

The codebase already proves the right pattern in one collection: `services/entities.py:340` and `services/entity_merge.py:165` use `uuid5(NAMESPACE_URL, f"entity::{user_id}::{name.lower()}")` for the `entities` collection. This decision generalises that good local pattern across the two remaining surfaces (`documents`, `conversations`) plus the one CalDAV signal-id site (`adapters/calendar_adapter.py:132`) that is also at risk for cross-user collision.

The decision is also constrained by a sequencing requirement from the in-flight `personal_shared_system_memory_scopes` exploration (*(maintainer-local only; not part of the tracked repository)*, ranked #1 follow-up). That exploration's Open Question #2 specifies: *"Recommend ordering: ship `per_user_file_index` first, then this exploration's plan."* If this chunk ships first, scopes is a clean `ALTER TABLE … ADD COLUMN scope` migration; if scopes ships first, `file_index` has to do double duty in one larger plan. This decision honours the recommended ordering.

**Greenfield reality (2026-04-19):** there are no real users yet, no live `file_index` content of consequence, no production `documents`/`conversations` points to migrate. This decision is therefore scoped to ship the intended end-state directly, with no backfill or upgrade-tooling burden. A short future-upgrade note is preserved in the exploration for whichever later plan first ships against a deployment carrying pre-namespace data; that tooling is **not built by this decision**.

## Decision

Adopt the exploration's **Option 1**, executed as a direct greenfield implementation:

1. **Postgres schema** — drop the bare-path `file_index_file_path_key` constraint and add a composite `UNIQUE(user_id, file_path)` via a new migration `postgres/migrations/011-per-user-file-index.sql` (idempotent — `IF EXISTS` / `IF NOT EXISTS` guarded). Align `postgres/init.sql` for fresh installs.
2. **Qdrant `point_id` namespace** — change the deterministic-id helpers so every namespaced collection includes `user_id`:
   - `documents`: `uuid5(NAMESPACE_URL, f"{user_id}::{file_path}::chunk-{i}")` (was `f"{file_path}::chunk-{i}"`)
   - `conversations`: `uuid5(NAMESPACE_URL, f"session::{user_id}::{session_id}")` (was `f"session::{session_id}"`)
   - `signals` (CalDAV adapter only): `uuid5(NAMESPACE_URL, f"caldav::{user_id}::{uid}")` (was `f"caldav::{uid}"`)
   - `entities` and other `signals` adapters are unchanged — `entities` already includes `user_id`; non-CalDAV `signal_id`s are uuid4-random.
3. **Idempotent ingest writes** — replace the precheck-then-`INSERT` pattern at `services/ingest.py:197-208` with `INSERT … ON CONFLICT (user_id, file_path) DO UPDATE SET file_hash = EXCLUDED.file_hash, chunk_count = EXCLUDED.chunk_count, updated_at = NOW()`, eliminating a small race window. Ships in the same change as the composite UNIQUE — no reason to split.

The Postgres `sessions` table primary key is **explicitly not changed** by this decision. `session_id` remains a single-column UUID PK; cross-user collision is foreclosed at the only surface that matters today (the deterministic Qdrant `conversations` `point_id`) by namespacing that id with `user_id`. Reasoning is recorded in the exploration §"Sessions key — explicitly out of scope".

The migration claims the `011-…` slot. The in-flight `personal_shared_system_memory_scopes` chunk shifts to `012-…`; the in-flight `per_user_connector_credentials` chunk slides from its prior `012-…` reservation to `013-…`. This ordering is endorsed by both adjacent explorations.

**Out of scope for this decision** (deliberately, given the greenfield framing):

- **No offline `backfill_qdrant_point_ids.py` script** — there is no live data to re-key.
- **No restore-path warning in `routes/admin.py`** — there are no pre-namespace backups to warn about.
- **No operator runbook in `docs/dev-cheatsheet.md` / `docs/connect-and-verify.md`** — fresh installs land directly on the end-state schema.
- **No `entity_relations.evidence_id` cleanup** — adjacent issue, not required to close B11/B12. Folded in only if it turns out to be ≤ ~5 lines while the related code is open; otherwise tracked as the `entity_relations_evidence_dedup` follow-up.
- **No `audio_memos` per-user namespace** — no live writer exists; deferred to whichever chunk lands the audio-capture route.

These are documented in the exploration's §"Future-upgrade note" and §"Follow-ups" so a later operator/plan can pick them up if the situation ever changes.

## Alternatives Considered

- **Option 2 — Content-addressable file dedupe (`file_owners` join table, single canonical chunk row per file body across owners).** Rejected: 5–10× the effort to close a bug graded "low complexity" by the audit; conflates shared-body with shared-scope (a decision that belongs in the scopes exploration, not here); rewrites the read path from single-step Qdrant filter to two-step Qdrant + Postgres join. See exploration §Option 2.
- **Option 3 — Per-user Qdrant collections (`documents_<user_id>`).** Rejected: this is the audit's C8 pattern, explicitly ruled out for family-LAN by the audit response §5.5 ("hosted-multi-tenant patterns that would actively hurt household sharing"); Qdrant 2026 multitenancy docs recommend payload-based partitioning over multiple collections for this scenario; doesn't even close B12 (Postgres `UNIQUE` is unchanged). See exploration §Option 3.
- **Option 4 — No-op + documentation.** Rejected: the failure is silent data corruption (Qdrant overwrite + Postgres `INSERT` failure); documentation does not fix corruption. Deferring also forces the scopes plan to do double duty. See exploration §Option 4.

Full evaluation lives in *(maintainer-local only; not part of the tracked repository)*.

## Consequences

**What becomes easier**

- Two household members can ingest the same path without losing each other's data; `re-ingest skip` works correctly per user.
- The `personal_shared_system_memory_scopes` plan becomes a clean `ALTER TABLE … ADD COLUMN scope` on `file_index` with no further `UNIQUE` redesign.
- Per-user backup re-import (audit B8 follow-up) becomes safe because both Postgres and Qdrant key spaces are now per-user.
- `INSERT … ON CONFLICT` eliminates a precheck-then-INSERT race within a single user as a free side effect.
- The "every deterministic Qdrant id includes `user_id`" rule becomes uniform across collections (matches the existing `entities` precedent).

**What becomes harder or impossible**

- Any future "share one canonical file body across multiple users" feature (content-addressable dedupe) is foreclosed as a *Lumogis* pattern. If household-wide dedupe ever becomes a real ask, the right answer is via the scopes column (`scope='shared'` on a family-shared file), not by collapsing per-user `file_index` rows. This is judged the right direction.
- Any future "per-user Qdrant collections" decision becomes a strict regression — payload partitioning with per-user namespaced ids supersedes that pattern.

**What future chunks must know**

- The deterministic Qdrant id for `documents` and `conversations` now includes `user_id`. Any chunk that writes to these collections must use the namespaced helper, not raw `uuid5`.
- Migration numbering: `011-per-user-file-index.sql` is reserved by this decision. `personal_shared_system_memory_scopes` shifts to `012-…`; `per_user_connector_credentials` shifts to `013-…`.
- `file_index` writes use `INSERT … ON CONFLICT (user_id, file_path) DO UPDATE`. Future ingest extensions must preserve this idempotency contract.
- **Future-upgrade tooling** — if/when Lumogis is first deployed against a household carrying meaningful pre-namespace `documents`/`conversations` content, an offline `backfill_qdrant_point_ids.py` re-keying script and a restore-path warning will need to be built then. Starting point is preserved in the exploration's §"Future-upgrade note". Not built by this decision.

## Revisit conditions

- **Revisit if** an external surface (MCP client, Lumogis Web, test harness) starts supplying deterministic `session_id`s that could collide cross-user — strengthen the `sessions` PK to `(user_id, session_id)` if collisions become measurable.
- **Revisit if** household-wide file dedup becomes a real product requirement (e.g. families consistently report storage pressure from duplicating large shared archives) — at that point the `file_owners` join pattern (exploration's Option 2) deserves its own exploration *layered on top of* the scopes column, not as a replacement for this decision.
- **Revisit if** Lumogis ever ships in a hosted multi-tenant configuration — the audit's C8 pattern (per-user Qdrant collections) would become the right call for physical isolation, but only in that deployment shape; family-LAN should remain on payload partitioning.
- **Revisit if** Qdrant 2.x changes its multitenancy guidance — current 2026 guidance is the basis for this decision and is unlikely to shift, but worth re-reading if a major version bump lands.

## Status history

- 2026-04-18: Draft created by `/explore per_user_file_index_and_ingest_attribution` (composer-2, Opus 4.7). Will be finalised by `/verify-plan` once the implementation lands.
- 2026-04-19: Greenfield simplification — removed Decision items 4 (offline backfill script) and 5 (restore-path warning) from the implementation scope, given that no current Lumogis installation carries live pre-namespace content. The architectural decision (Option 1) is unchanged; only the rollout scope has tightened. Future-upgrade tooling preserved as non-blocking guidance in the exploration. Updated by composer-2 (Opus 4.7).
- 2026-04-19: Finalised by `/verify-plan` — implementation confirmed all three Decision items (composite `UNIQUE(user_id, file_path)`; Qdrant `user_id`-namespaced point ids for `documents`, `conversations`, CalDAV `signals`; `INSERT … ON CONFLICT (user_id, file_path) DO UPDATE`). `sessions` PK kept as `session_id`-only per the explicit out-of-scope note. `entity_relations.evidence_id` cleanup deferred per the soft-guard exit (now a named follow-up `entity_relations_evidence_dedup`); migration `011-per-user-file-index.sql` claims the slot as planned. Finalised copy at `docs/decisions/013-per-user-file-index-and-ingest-attribution.md`. composer-2 (Opus 4.7).
