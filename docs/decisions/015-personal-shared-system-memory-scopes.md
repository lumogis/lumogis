# ADR 015 — Personal / Shared / System memory scopes

**Status:** Finalised — all acceptance criteria green
**Decided by:** composer-2 (Opus 4.7) running `/explore personal_shared_system_memory_scopes`
**Created:** 2026-04-18
**Last updated:** 2026-04-19 (closure pass — acceptance #4 resolved; `tools.py` symmetry fix)

> **Note for future readers.** This ADR finalises the architectural decision recorded in
> *(maintainer-local only; not part of the tracked repository)* (Draft (revised)). The decision shipped
> intact. The implementation deviation originally recorded against acceptance criterion #4 (52
> untagged raw `WHERE user_id` sites) was **resolved on 2026-04-19 in a fix-only closure pass**
> (16 files retagged or rewritten through `visible_filter` / `AND scope = 'personal'`; CI gate
> `orchestrator/tests/test_no_raw_user_id_filter_outside_admin.py` is now green). A
> Postgres↔Qdrant visibility-symmetry asymmetry in `services/tools.py:_query_entity` was also
> caught and fixed in the same pass — the Qdrant semantic fallback now resolves through
> `visible_qdrant_filter`, mirroring the Postgres `visible_filter` path, so a `shared`/`system`
> entity is reachable by either exact-name OR semantic lookup. See "Closure pass" below for the
> per-category breakdown; "Implementation deviation (now resolved)" preserves the original
> deviation record for historical context.

## Context

The Family-LAN multi-user plan (`docs/decisions/012-family-lan-multi-user.md`) closed the
*isolation* gap (Alice cannot see Bob's data) but exposed the *sharing* gap: every memory surface
(Postgres notes/audio/sessions/file_index/entities/signals/review_queue/action_log/audit_log,
FalkorDB KG, Qdrant `documents`/`conversations`/`entities`/`signals`) filtered on `user_id` only.
The result was a set of disconnected per-user silos — Alice ingests the family meal plan; Bob asks
"what's for dinner Friday"; Bob gets nothing because the meal plan is tagged `user_id=alice`.

The `MULTI-USER-AUDIT-RESPONSE.md` §5.5 ranked closing this as the **single most important
deferred item for credible household use of Lumogis**.

Constraints that shaped the option space:

- **Local-first, single-tenant family-LAN** — not hosted multi-tenant SaaS. Patterns that earn
  their cost only at SaaS scale (per-tenant physical isolation, per-item ACL with thousands of
  principals) are wrong-sized.
- **Three independent stores** must enforce the same rule (Postgres, Qdrant, FalkorDB) —
  centralisation matters more than store-native features.
- **FalkorDB cannot cross-graph union** (verified: FalkorDB Multigraph Topology page + GitHub
  Discussion #791) — graph-per-scope is foreclosed because it would actively break the union
  retrieval the project is trying to enable.
- **Personal-by-default privacy posture** — sharing must be opt-in per item; no row should
  accidentally become household-visible during the migration.
- **Backward compatibility with the family-LAN floor** — the existing isolation tests must
  continue to pass; a `personal` row must remain visible only to its owning `user_id`.
- **Greenfield for scope** — no existing `scope` / `visibility` / `shared` / `tenant` column
  anywhere; only one weak precedent (`signals.source_id='__system__'` as a string sentinel).

## Decision

Add **two columns** to every memory surface — `scope TEXT NOT NULL DEFAULT 'personal' CHECK
(scope IN ('personal','shared','system'))` and a nullable `published_from` reference back to the
personal source row — across all three stores (Postgres tables, Qdrant payloads, FalkorDB
node/edge properties). Centralise the read-time visibility rule in two new helper modules —
`orchestrator/visibility.py` (Core) and `services/lumogis-graph/visibility.py` (KG mirror) —
exposing `visible_filter(user)`, `visible_qdrant_filter(user)`, and `visible_cypher_fragment(user)`,
each emitting a store-specific predicate implementing:

```
(scope = 'personal' AND user_id = $me) OR scope IN ('shared', 'system')
```

Admin god-mode reads use a **separate** family of helpers — `admin_unfiltered_filter(user)`,
`admin_unfiltered_cypher_fragment(user)` — so the bypass surface is enumerable in code review (see
"Implementation deviation" for why this matters).

**Sharing is publish/unpublish (projection model), not destructive mutation.** The personal row
is the source of truth and is never mutated by a share/unshare. "Sharing" creates a separate
shared projection row/point/node tagged `scope='shared'` and (in Postgres/Qdrant) carrying
`published_from` pointing back to the personal source. "Unsharing" deletes the projection while
leaving the personal source intact. In FalkorDB this falls out of a conditional MERGE-key:
`personal` MERGE on `(lumogis_id, user_id)` produces the personal node; `shared`/`system` MERGE
on `(lumogis_id, scope)` produces the household-canonical projection.

Cardinality is **3 in v1** (`personal`/`shared`/`system`) with a forward-compatible CHECK
constraint — a future migration can extend to `'public'` without data changes. **No new env vars
in v1**: default ingest scope is hard-coded `personal`, sharing is a post-hoc explicit user act.
**`scope` is a required field** on every MCP tool result so downstream LLM reasoning can
distinguish personal/shared/system; raw other-user identifiers and `published_from` are not
exposed in v1. **Admin god-mode reads apply only on admin/audit/review/operational routes**; on
normal retrieval surfaces (`/api/v1/*`, `/search`, `/entities`, `/signals`, MCP), admins follow
the same `visible_filter` rule as regular users.

App-scope intelligence (household digest, shared-scope relevance boost, auto-promotion
suggestions, household-graph viz, cross-user redaction in chat) is signposted as a separate
lumogis-app concern that consumes this column; it is **not** part of this decision.

The migration lands as **`postgres/migrations/013-memory-scopes.sql`** (the originally drafted
`012` slot was claimed by `entity_relations_evidence_uniq.sql`).

## Alternatives Considered

- **Postgres Row-Level Security** — works for the 9 SQL tables but does not extend to Qdrant or
  FalkorDB, so the application-side helper has to exist anyway. The "invisible WHERE" property
  is a debugging liability in a single-tenant codebase. Reduces to the chosen option plus
  middleware cost.
- **Notion/Confluence-style per-item ACL** — flexible but ~3–4 weeks of work, requires a
  non-trivial UI surface, and is wildly oversized for households where principal cardinality is
  2–8. Revisit only if households consistently report needing per-person granularity after v1
  ships.
- **Per-scope physical isolation** (separate Qdrant collections / FalkorDB graphs / Postgres
  partitions) — explicitly the audit's hosted-multi-tenant pattern, ruled out by the audit
  response, by Qdrant's 2026 multitenancy docs ("minimize collections"), and most decisively by
  FalkorDB's lack of cross-graph query support, which would force every union retrieval through
  application-side merge.
- **Two-value enum** (`personal`/`shared` only) — folds "system" into "shared", losing the
  distinction between Lumogis-discovered baseline facts and human-curated household knowledge;
  has no existing precedent.
- **Four-value enum** (`personal`/`shared`/`system`/`public`) — `public` has no concrete v1 use
  case; ship 3 with a CHECK constraint extensible to 4 if and when public-KB ingestion lands.

## Closure pass — 2026-04-19

The acceptance #4 deviation recorded by `/verify-plan` was resolved in a fix-only pass on the
same day. The architectural decision was not changed; only call sites were either rewritten
through the existing helpers or annotated with the plan's escape-hatch tags. Two findings worth
recording for future readers:

**1. Acceptance #4 (`visible_filter` adoption + escape-hatch tagging) — green.**
The CI gate `orchestrator/tests/test_no_raw_user_id_filter_outside_admin.py` ran from ~52 untagged
hits down to **0**. 16 files touched, broken down by category:

- **`# ADMIN-BYPASS:` (12 sites)** — `orchestrator/routes/admin.py`
  (`/admin/graph/health`, `/admin/dedup/run`, `/admin/export`) and
  `services/lumogis-graph/routes/graph_admin_routes.py` (`/graph/health`). These are explicit
  admin/audit/review surfaces per plan §2.8; SQL shape preserved.
- **`# SCOPE-EXEMPT:` (10 sites)** — reads against the per-§2.10 scope-less tables
  (`sources`, `relevance_profiles`, `routines`, `routine_do_tracking`, `feedback_log`,
  `app_settings`, `kg_settings`, `constraint_violations`, `known_distinct_entity_pairs`,
  `dedup_candidates`, `deduplication_runs`, `review_decisions`) and against `entity_relations`
  (which has no `scope` column of its own — visibility inherits from endpoints, plan §2.4
  rule 9). Files: `routes/signals.py`, `services/routines.py`, `services/signal_processor.py`,
  `services/deduplication.py`, `services/entity_merge.py`,
  `services/lumogis-graph/quality/deduplication.py`,
  `services/lumogis-graph/quality/edge_quality.py`,
  `services/lumogis-graph/quality/entity_constraints.py`.
- **Rewritten through `visible_filter` (3 sites)** — `services/tools.py:_query_entity`
  Postgres lookup, `services/routines.py:_run_weekly_review` (`signals` + `entities` reads).
  These are user-facing retrieval surfaces where the household union is the correct semantics.
- **Narrowed with `AND scope = 'personal'` + `# SCOPE-EXEMPT:` (multiple sites)** —
  `services/entity_constraints.py`, `services/deduplication.py`,
  `services/lumogis-graph/graph/writer.py:_resolve_entity_names`, and the corresponding
  `services/lumogis-graph/quality/*` mirrors. Plan §2.11 specifies that dedup, entity merge,
  and corpus-level constraint checks remain personal-scope-only — the explicit `AND
  scope = 'personal'` narrowing makes that intent unambiguous in code.

The grep gate's `_LOOKBACK = 6` lines required a small mechanical wrinkle: in multi-line SQL
string literals, the tag must sit **immediately above** the `WHERE user_id` line. This was
achieved by inserting comments mid-concatenation (e.g. `"FROM x " "\n# SCOPE-EXEMPT: …\n"
"WHERE user_id = %s "`), which is valid Python adjacent-string-literal syntax.

**2. Postgres↔Qdrant visibility symmetry in `services/tools.py:_query_entity` — fixed.**
The closure pass rewrote the Postgres exact-name lookup through `visible_filter` (so Bob's
exact-name lookup of "Friday meal plan" finds Alice's `shared` entity), but the Qdrant
semantic fallback was left on a raw `user_id`-only payload filter. The gap was caught and
fixed in the same pass: the fallback now resolves through `visible_qdrant_filter`, mirroring
the Postgres path. A regression test (`test_tools_query_entity_qdrant_fallback_uses_visible_filter`
in `orchestrator/tests/test_entities.py`) pins both halves of the contract by source
inspection so the two paths cannot drift apart again. The long "cross-user safety" comment on
the `entity_relations` SELECT was also rewritten — it previously justified the missing
`WHERE user_id` predicate by appealing to a `WHERE user_id = %s` upstream filter that no
longer exists; the updated text appeals to the visibility contract (own personal + all
shared/system) and to plan §2.4 rule 9 (`entity_relations` has no `scope` column; visibility
inherits from endpoints).

**Final test result: 788 passed, 9 skipped, 0 failures.** Acceptance criterion #4 now passes;
all 12/12 plan acceptance criteria are green.

## Implementation deviation (now resolved — preserved for historical context)

> **Status:** RESOLVED in the 2026-04-19 closure pass above. Preserved as written for the
> historical record of what `/verify-plan` initially observed.

The architectural decision shipped intact. One execution-time deviation was originally
observed by `/verify-plan`:

**Acceptance #4 — universal `visible_filter` adoption.** Plan §10 #4 mandates that every raw
`WHERE user_id …` predicate in `orchestrator/services/`, `orchestrator/routes/`, and
`services/lumogis-graph/` either be replaced by a `visible_*` helper, be tagged
`# ADMIN-BYPASS:` (admin/audit/review god-mode), or be tagged `# SCOPE-EXEMPT:` (scope-less
table per plan §2.10). The CI gate `tests/test_no_raw_user_id_filter_outside_admin.py` was
**not** written during implementation. `/verify-plan` added it; on first run it failed with
**52 untagged hits**.

The 52 hits decomposed roughly as:

- **~22 hits** in `orchestrator/routes/admin.py` and `services/lumogis-graph/routes/graph_admin_routes.py`
  — admin/audit/review surfaces that should keep their current SQL but need a one-line
  `# ADMIN-BYPASS:` annotation per the plan's escape-hatch convention.
- **~3 hits** in `routes/signals.py` and `services/routines.py` reading from scope-exempt tables
  (`relevance_profiles`, `routines`) per plan §2.10 — need a `# SCOPE-EXEMPT:` annotation.
- **~25 hits** in `services/edge_quality.py`, `services/entity_constraints.py`, `services/tools.py`,
  `services/signal_processor.py`, `services/deduplication.py`, `services/entity_merge.py`, and
  the corresponding `services/lumogis-graph/quality/*` mirrors that need either a `visible_filter`
  rewrite or an `AND scope = 'personal'` narrowing per plan §2.11 (dedup is personal-scope only).

This deviation did **not** invalidate the architectural decision. The visibility helper, the
projection engine, the publish/unpublish API, the migration, the FalkorDB MERGE-key strategy, the
admin god-mode separation, and the deterministic legacy-user remap were all in place and
exercised by tests. The deviation was a **completeness gap** in the read-site rewrite phase of
§9 step 3 — the plan listed the files to refactor; not every read site in those files was
rewritten or tagged. The CI gate exists and was flipped from failing to passing in the closure
pass above.

## Consequences

**What becomes easier:**

- **Household sharing actually works for the rewritten surfaces.** Alice marks the meal plan
  `shared`; Bob's reads through `routes/data.py`, `routes/signals.py`, `services/memory.py`,
  `services/search.py`, `services/entities.py`, `services/ingest.py`, and the MCP tools
  (`memory.search`, `memory.get_recent`, `entity.lookup`, `entity.search`) honour the union
  semantics correctly. The publish/unpublish round-trip is reversible, idempotent, and tested
  end-to-end in `tests/integration/test_household_sharing.py`.
- **System-scope facts get a home.** `signals.source_id='__system__'` rows backfill to
  `scope='system'` and become visible to everyone without leaking into per-user attribution.
- **App-scope intelligence becomes feasible** — household digest, shared-scope relevance boost,
  auto-promotion, household-graph viz, cross-user redaction — all five plug-points enumerated in
  the exploration are unblocked (though out of scope here).
- **The visibility rule is in one helper, not three.** Future changes (add `'public'`, change the
  union, add an admin-bypass mode) are one-file edits.

**What becomes harder:**

- **Per-item ACL becomes harder to layer in v2** if households want per-person granularity.
  Mitigation: the `Scope` enum can grow without breaking anything; a future `acl_entries` table
  can co-exist as an "advanced sharing" override.
- **Every read site needs to be either rewritten or tagged.** Acceptance #4 exists precisely to
  enforce this; the implementation deviation above means the work is partially complete.
- **Entity dedup interaction.** Today dedup operates per-user on personal-scope rows only;
  shared/system rows are never dedup candidates (plan §2.11). Merge sweeps `published_from`
  references across all six projection-capable tables in the same transaction.
- **Test surface grows.** Headline integration test (`test_household_sharing.py`, 11 named
  scenarios + 3 docker-only skipped pins) plus per-surface unit tests for the new helper.

## Revisit conditions

- **Per-item granularity** — if households consistently report needing "share this with mom but
  not the kids" as a top-3 friction after v1 ships, revisit Notion-style ACL as a layered
  "advanced sharing" mode (ACL table that overrides the enum for opted-in items), not as a
  replacement.
- **Public knowledge bases** — if a public-KB ingestion path ever lands (Wikipedia dump, news
  archive, public RSS bulk import), revisit the CHECK constraint to admit `'public'`. Pure schema
  migration; no other code change.
- **Hosted-tenant pivot** — if Lumogis ever runs as hosted multi-tenant, revisit Postgres RLS and
  per-tenant physical isolation. Both become *defensible* in a multi-tenant context; both remain
  *wrong* for family LAN.
- **FalkorDB cross-graph queries** — if FalkorDB ever supports cross-graph union, revisit
  graph-per-scope as a defence-in-depth option for the KG layer specifically.
- **Dedup pipeline change** — if the entity dedup pipeline gains household-aware resolution
  (so two users' "John" mentions can be unified into a single shared entity), revisit the
  FalkorDB MERGE-key decision.

## Status history

- **2026-04-18:** Draft created by `/explore` (composer-2, Opus 4.7). Recommendation: Option 1
  with cardinality 3, application-side helper, payload+property pattern across the three stores,
  all-personal backfill, personal-by-default writes.
- **2026-04-18 (revision):** All 8 open questions resolved (D1–D8); Amendment A (publish/unpublish
  projection model) and Amendment B (system as first-class) folded in. Migration originally
  numbered `012-memory-scopes.sql`.
- **2026-04-19:** Revised during `/review-plan --arbitrate R1` — visibility helper API moved to
  separate `admin_unfiltered_*()` helpers (not the originally drafted `bypass_for_admin=False`
  parameter); migration filename updated to `013-memory-scopes.sql` (012 slot taken by
  `entity_relations_evidence_uniq`).
- **2026-04-19:** Finalised by `/verify-plan` (composer-2, Opus 4.7). Architectural decision
  intact. One implementation deviation recorded above (acceptance #4 — 52 untagged sites and a
  newly-added failing CI gate).
- **2026-04-19 (closure pass):** Acceptance #4 deviation resolved (composer-2, Opus 4.7) —
  16 files retagged or rewritten through `visible_filter` / `AND scope = 'personal'`; CI gate
  green (0 untagged sites). Postgres↔Qdrant visibility-symmetry asymmetry in
  `services/tools.py:_query_entity` Qdrant fallback caught and fixed in the same pass; pinned
  by a new source-inspection regression test
  (`test_tools_query_entity_qdrant_fallback_uses_visible_filter`). All 12/12 acceptance
  criteria green; 788 orchestrator tests pass.
