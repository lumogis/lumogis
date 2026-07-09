# ADR 126: Richer capability-tool unavailable reasons (LUM-61)

**Status:** Finalised

**Created:** 2026-06-24

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-24

**Plan:** none — shipped on `claude/lum-61-tool-unavailable-reasons` before formal plan / verify

**Exploration:** `.cursor/explorations/lum-61-tool-unavailable-reasons-retro.md`

**Issue:** [LUM-61](https://linear.app/lumogis/issue/LUM-61)

## Context

`GET /api/v1/me/tools` lists capability-backed tools with a `why_not_available` string when a service is unhealthy. Before this change the string was a generic "not healthy", which gave operators and the Web UI no actionable signal (timeout vs auth vs HTTP status).

## Decision

1. **`RegisteredService.last_unhealthy_reason`** — health probes in `capability_registry.py` capture a structured reason on failure (timeout, connection refused, network error, credentials rejected for HTTP 401/403, or `HTTP <status>`).
2. **`unified_tools.py`** — surfaces that reason in `why_not_available` when present; preserves existing API shape (content-only change).
3. **Tests** — `test_capability_health.py` exercises probe failure paths; `test_api_v1_me_tools.py` extended.

## Consequences

- **Positive:** Me → Tools and API consumers see specific failure modes without new fields.
- **Limits:** Reason text is best-effort from the last probe; no historical probe log in v1.

## Status history

- 2026-06-24: Merged to `dev` from `claude/lum-61-tool-unavailable-reasons`; record-retro finalised ADR 126.
