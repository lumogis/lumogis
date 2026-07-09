# ADR-157: Post-ship household sharing fixes (LUM-157 coalescing + LUM-577 KG visibility)

**Status:** Finalised
**Created:** 2026-07-07
**Last updated:** 2026-07-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-07 (Composer)
**Plan:** none — shipped before formal plan / verify cycle for this slice
**Exploration:** `.cursor/explorations/lum_157_577_post_ship_fixes_retro.md`
**Draft mirror:** `.cursor/adrs/lum_157_577_post_ship_fixes.md`

## Context

After LUM-157 (ADR 155 content projection) and LUM-577 (admin invite-time `allows_shared` toggle) landed on `dev`, a Cursor bug-investigation branch found two post-ship gaps:

1. **Share job coalescing:** `_enqueue_share_job` reused any in-flight share/unshare job for a document. Unsharing while a share job was still running could coalesce onto the share job, dropping the unshare intent.
2. **KG visibility parity:** `services/lumogis-graph/visibility.py` defaulted `allows_shared=True` when unset on `UserContext`, and `visible_qdrant_filter` / `visible_cypher_fragment` always included the shared scope arm — unlike orchestrator paths that honour `users.allows_shared` for personal-only members.

## Decision

1. **Same-direction coalescing only:** `_fetch_inflight_share_jobs` exposes job `kind`; `_enqueue_share_job` returns the existing job only when `existing.get("kind") == kind`.
2. **KG DB mirror for `allows_shared`:** when `UserContext.allows_shared` is `None` and the user is not admin, read `allows_shared` from `users` via the KG metadata store; apply personal-only filtering in Qdrant and Cypher helpers (exclude shared arm; shared-scope filter returns empty for personal-only members).

## Alternatives Considered

- **Queue cancellation of in-flight share on unshare** — heavier; risks partial projection state. Rejected in favour of enqueueing a distinct unshare job.
- **Assume `allows_shared` always set on KG `UserContext`** — would require auditing every KG route; rejected — lazy DB read matches orchestrator fallback.

## Consequences

**Easier:** rapid share/unshare toggles behave correctly; personal-only invitees do not retrieve shared content through KG CONTEXT_BUILDING paths.

**Harder / cost:** extra Postgres read on KG visibility when `allows_shared` is unset.

**Future chunks must know:** coalescing is per `kind`; KG visibility must stay aligned with `orchestrator/visibility.py` when household RBAC evolves.

### As-implemented surface

- `orchestrator/services/documents.py` — `kind` in inflight map; kind-aware coalesce guard.
- `services/lumogis-graph/visibility.py` — `_get_allows_shared_from_db`, updated `visible_qdrant_filter` / `visible_cypher_fragment`.
- Tests: `test_unshare_while_share_inflight_enqueues_unshare_not_coalesced`; `services/lumogis-graph/tests/test_visibility.py`.

### Testing retrospective

Targeted pytest: 1 share coalescing test + 4 KG visibility tests passed (2026-07-07). No new integration test for the coalescing race.

## Revisit conditions

- Measured KG visibility latency from DB lookups → propagate `allows_shared` on all KG `UserContext` construction sites.
- New share job kinds → extend coalescing contract and tests.

## Linear linkage (Product OS)

- **LUM-157** — post-ship coalescing fix (comment on issue, 2026-07-07)
- **LUM-577** — KG `allows_shared` mirror (comment on issue, 2026-07-07)
- **New issue needed:** no

## Status history

- 2026-07-07: Finalised by /record-retro (retrospective) after merge to `dev` @ `5dd4a0599`.
