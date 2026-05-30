# ADR 067: LUM-281 — paperless poll stall on blocked-high external ingest (as-shipped)

**Status:** Finalised
**Created:** 2026-05-27
**Last updated:** 2026-05-27
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-27 (Composer)
**Plan:** none — shipped via Cursor agent branch before formal plan / verify for this slice
**Exploration:** `.cursor/explorations/lum_281_paperless_blocked_high_poll_stall_retro.md`
**Draft mirror:** `.cursor/adrs/lum_281_paperless_blocked_high_poll_stall.md`
**Linear:** [LUM-281](https://linear.app/lumogis/issue/LUM-281/feature-paperless-ngx-lumogis-ingest-v01-docker-hn-persona-a)

## Context

**LUM-281** v0.1 ingest (**ADR 054**) and the pagination watermark fix (**ADR 062**) use strict **`added__gt=<since_cursor>`** polling. When **`ingest_external_document`** hits **`drops_blocked_high`** (embedder backpressure), it returns without advancing **`sources.poll_cursor`** but also without **`skipped=True`**. The poller continued processing later rows in the same tick; a subsequent document could advance **`poll_cursor`** past the blocked row's **`added`** while that row was never reconciled — strict **`added__gt`** would then omit it permanently.

Fix landed on **`dev`** via **`cursor/critical-correctness-bugs-8ce7`** (commit **`602fbe4a8`**, cherry-pick **`b8d006d70`**, 2026-05-27) without a Product OS plan/verify loop.

## Decision

1. **`ingest_external_document`** — blocked-high path returns **`advance_external_poll_cursor=False`** explicitly on **`IngestResult`**.
2. **`orchestrator/signals/feed_monitor.py::_poll_paperless_source`** — after each ingest, if **`not result.advance_external_poll_cursor and not result.skipped`**, set **`hard_stop = True`** and break the document loop (same tick ends; Postgres cursor unchanged for the stalled row).
3. Regression test **`orchestrator/tests/test_paperless_feed_pagination.py::test_paperless_poll_stalls_tick_when_external_ingest_blocked_high`**.

## Alternatives considered

- **Continue tick after blocked-high** (pre-fix) — rejected; allows silent permanent skip.
- **Mark blocked-high as `skipped=True` with cursor advance** — rejected; row was not successfully ingested or reconciled.
- **Retry blocked-high inline in same tick** — rejected; embedder backpressure needs scheduler deferral; stall + next tick is sufficient for v0.1.

## Consequences

**Positive:** Blocked-high rows are not dropped when later documents share the same poll tick; complements **ADR 062** page-stable watermark.

**Limits:** Unit mock test only; per-tick caps unchanged.

## Revisit conditions

- **`feed_monitor`** or **`ingest_external_document`** refactor — preserve stall invariant; keep pagination tests green.
- Missing-document reports after blocked-high ingest — add compose integration against live paperless-ngx.
- paperless filter semantics change — revisit with **ADR 054** / **062**.

## Linear linkage (Product OS)

- **LUM-281:** parent ingest programme — post-ship correctness (comment via **`/linear-update`**, not a new issue required).
- **New issue needed:** no

## Testing retrospective

**`.venv/bin/python -m pytest orchestrator/tests/test_paperless_feed_pagination.py -q`** — **2 passed** on merged **`dev`**. Full **`make test`** not re-run for this retro slice.

## Status history

- **2026-05-27:** Finalised by **`/record-retro`** — cherry-pick **`b8d006d70`** on **`dev`**.
