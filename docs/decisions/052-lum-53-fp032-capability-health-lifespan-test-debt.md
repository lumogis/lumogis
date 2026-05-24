# ADR-052: LUM-53 / FP-032 — `test_capability_health.py` vs lifespan auto-discovery (obsolete close)

**Status:** Accepted
**Created:** 2026-05-17
**Last updated:** 2026-05-17
**Decided by:** `/explore --headless` LUM-53 (draft); **`/verify-plan --headless`** finalisation
**Linear:** [LUM-53](https://linear.app/lumogis/issue/LUM-53/fp-032-test-debt-capability-health-vs-lifespan) (parent [LUM-78](https://linear.app/lumogis/issue/LUM-78/capability-launchers-gateway-programme-mcp-tools-connector-registry-capability-invoke))

## Context

FP-032 (BL-032) recorded test debt: `test_capability_health.py` failed when FastAPI lifespan auto-registered a live capability service via `CAPABILITY_SERVICE_URLS` and overwrote hand-seeded `CapabilityRegistry` fixtures.

Since then, the orchestrator ships a lifespan guard (`get_capability_service_urls()` non-empty before `discover()`), `CapabilityRegistry.discover` / `discover_sync` no-op on empty URL lists, and `orchestrator/tests/conftest.py` clears `CAPABILITY_SERVICE_URLS` and `GRAPH_MODE` at import time before capability registry modules load. The historical clobber path is therefore not reachable in the as-shipped tree.

**ADR numbering:** **052** is used here because the topic index already reserves **051** for in-flight **LUM-210** work on this branch line; there is no `docs/decisions/051-*.md` in this checkout yet.

## Decision

Close **LUM-53 / FP-032** as **obsolete** with pytest evidence. Do **not** add new product code or dedicated regression tests for Option 1. The contract (empty configured URLs ⇒ lifespan does not call `discover()` on the singleton; conftest prevents stack env from populating URLs during unit tests; existing capability health tests seed the singleton before `TestClient`) is the shipped seam.

## Alternatives Considered

- **Dedicated regression test** (exploration Option 2) — deferred unless a future pytest failure proves the seam fragile; if needed, track as a **child of LUM-78**.
- **Hold LUM-53 until LUM-78 completes** (Option 3) — rejected; LUM-78 is an umbrella, not a gate for this hygiene close.

## Consequences

**Positive:** Portfolio / Linear can drop a stale row; readers anchor on `CapabilityRegistry` docstring, conftest, and existing tests per **ADR 010** (`010-ecosystem-plumbing.md`).

**Risk:** None for this close — no runtime change. If the lifespan guard, conftest env-clear, or empty-URL early-return in discovery is removed, existing tests should fail first; add Option 2 then.

## Revisit conditions

- Pytest fails in `orchestrator/tests/test_capability_health.py` or `orchestrator/tests/test_capability_registry.py` for reasons traceable to lifespan vs fixture interaction — escalate Option 2 under **LUM-78**.
- Removal of the `if capability_urls:` (or equivalent) lifespan branch, removal of the conftest env-clear, or removal of empty-list early-return in `CapabilityRegistry.discover` / `discover_sync` — treat as reopening the original failure mode; add a guard test and a new tech-debt issue.
- **LUM-78** delivers a materially different startup registry contract — re-evaluate this ADR’s status.

## Status history

- **2026-05-17:** Finalised by **`/verify-plan --headless`** — `pytest tests/test_capability_health.py tests/test_capability_registry.py` **30 passed**; **`make test`** **1739 passed**, **51 skipped** (orchestrator + stack-control) using project venv under `orchestrator/.venv`; implementation confirms obsolete-close decision.
- **2026-05-17:** Draft created by **`/explore --headless`** LUM-53.
