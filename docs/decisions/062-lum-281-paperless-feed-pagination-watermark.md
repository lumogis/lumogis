# ADR 062: LUM-281 — paperless feed pagination watermark (as-shipped)

**Status:** Finalised
**Created:** 2026-05-23
**Last updated:** 2026-05-23
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-23 (Composer)
**Plan:** none — shipped via Cursor agent branch before formal plan / verify for this slice
**Exploration:** `.cursor/explorations/lum_281_paperless_feed_pagination_watermark_retro.md`
**Draft mirror:** `.cursor/adrs/lum_281_paperless_feed_pagination_watermark.md`
**Linear:** [LUM-281](https://linear.app/lumogis/issue/LUM-281/feature-paperless-ngx-lumogis-ingest-v01-docker-hn-persona-a)

## Context

**LUM-281** v0.1 ingest (**ADR 054**) polls paperless-ngx with **`added__gt=<since_cursor>`** over paginated **`/api/documents/`** results. The initial implementation advanced the in-memory **`since_cursor`** passed to **`fetch_documents_page`** after each ingested row when **`ingest_external_document`** signalled **`advance_external_poll_cursor`**. Because **`added__gt` is strict**, page *N+1* could omit every document sharing the same **`added`** timestamp as rows on earlier pages — a silent skip typical after bulk imports. Fix landed on **`dev`** via **`cursor/critical-correctness-bugs-6eb7`** (commit **`d2b323cf2`**, merge **`59476cd31`**) without a Product OS plan/verify loop.

## Decision

In **`orchestrator/signals/feed_monitor.py::_poll_paperless_source`**:

1. Capture **`fetch_since = cursor`** once per poll tick (from **`_fetch_poll_cursor`** at tick start).
2. Pass **`since_cursor=fetch_since`** to every **`PaperlessPoller.fetch_documents_page`** call in that tick.
3. **Do not** update in-memory **`source.poll_cursor`** between pages based on per-document ingest results; **`ingest_external_document`** continues to persist **`sources.poll_cursor`** in Postgres per document.

Add regression test **`orchestrator/tests/test_paperless_feed_pagination.py::test_paperless_poll_freezes_since_cursor_across_pages`**.

## Alternatives considered

- **Advance watermark between pages** (pre-fix behaviour) — rejected; breaks strict **`added__gt`** pagination when timestamps collide.
- **Switch to webhook-only ingest for bulk libraries** — out of scope for v0.1 (**ADR 054**); polling remains default.

## Consequences

**Positive:** Multi-page poll ticks ingest all pages for a shared **`added`** timestamp batch; aligns runtime behaviour with **ADR 054** incremental poll intent.

**Limits:** Unit-level mock test only; scheduler per-tick caps unchanged.

## Revisit conditions

- paperless API filter/ordering semantics change — revisit watermark strategy with **ADR 054**.
- Missing-document reports after bulk import — add compose integration against live paperless-ngx.
- Pagination refactor in **`feed_monitor`** — preserve **`fetch_since`** invariant and keep regression test green.

## Linear linkage (Product OS)

- **LUM-281:** parent ingest programme — this fix is post-ship correctness under that umbrella (comment via **`/linear-update`**, not a new issue required).
- **New issue needed:** no

## Testing retrospective

**`.verify-venv/bin/python -m pytest orchestrator/tests/test_paperless_feed_pagination.py -q`** — **1 passed** on merged **`dev`**. Full **`make test`** not re-run for this retro slice.

## Status history

- **2026-05-23:** Finalised by **`/record-retro`** — product merge **`59476cd31`** on **`dev`**.
