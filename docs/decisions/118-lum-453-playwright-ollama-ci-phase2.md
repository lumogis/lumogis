# ADR-118: Playwright Ollama mutations CI — Phase 2 (LUM-453)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-453](https://linear.app/lumogis/issue/LUM-453)

**Related:** [ADR-087](087-lum-450-playwright-ollama-mutations-e2e.md) (Phase 1 manual gate, LUM-450)

## Context

LUM-450 Phase 1 shipped manual `web-e2e-ollama-prove`. LUM-453 adds optional CI workflow + compose overlay per ADR-087 Phase 2.

## Decision

- **`.github/workflows/web-e2e-ollama.yml`** — optional; label / `workflow_dispatch` gated; not in default slim `web-e2e.yml`.
- **`docker-compose.web-e2e-ollama-ci.yml`** — full stack + Ollama for mutation spec.
- **CONTRIBUTING.md** — documents opt-in CI path.

**Not shipped:** `verify-public-rc-full` auto-wire (still deferred per ADR-087 until cold-pull baseline on CI).

## Consequences

- **Easier:** Repeatable Ollama pull/delete e2e on demand without polluting slim web-e2e CI.
- **Harder:** Multi-minute cold pulls; first green `workflow_dispatch` still operator-gated.

## Status history

- 2026-06-22: Finalised by `/record-retro` — on `dev` @ `c1d30c1d4`.
