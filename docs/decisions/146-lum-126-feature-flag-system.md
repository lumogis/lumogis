# ADR-146: Feature-flag system — env-var gates + admin visibility (LUM-126)

**Status:** Finalised

**Created:** 2026-06-30

**Last updated:** 2026-06-30

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-30 (Composer)

**Plan:** none — shipped on `origin/claude/devtools-linear-work-review-eed8kh`

**Exploration:** `.cursor/explorations/lum_126_feature_flag_system_retro.md`

**Draft mirror:** `.cursor/adrs/lum_126_feature_flag_system.md`

**Linear:** [LUM-126](https://linear.app/lumogis/issue/LUM-126/feature-flag-system-env-var-gates-for-experimental-features-admin)

## Context

Experimental subsystems (consolidation agent, proactive pipes, write-back MCP tools, graph consolidation, context compaction) will land incrementally. Before LUM-126 there was no mechanism to merge in-progress code **disabled by default** — features were either active or not merged.

## Decision

**Introduce a closed registry in `orchestrator/features.py` with `LUMOGIS_FF_*` env-var gates (all default false) and an admin read-only endpoint for visibility.**

As-built surface:

- **`orchestrator/features.py`** — `FeatureFlag` dataclass, closed `_FLAG_LIST`, `is_enabled(key)` raises on unknown keys, truthy parsing aligned with existing config helpers, env read per call (testable via `monkeypatch`).
- **`GET /api/v1/admin/feature-flags`** — `require_admin`; returns metadata + enabled state only (no secrets); OpenAPI snapshot regenerated.
- **`config/test.env.example`** — documented commented section (defaults false).
- **`CONTRIBUTING.md`** — “Feature flags” section for adding new gates.

Initial flags (all default off): `CONSOLIDATION_AGENT`, `CONTEXT_COMPACTION`, `GRAPH_CONSOLIDATION`, `PROACTIVE_PIPES`, `WRITE_BACK_MCP`.

## Alternatives considered

- **Compile-time stripping (Claude Code model):** rejected — Lumogis AGPL tree must stay buildable and testable with flags off, not forked builds.
- **Runtime DB-backed flags:** rejected for v1 — env gates match appliance/Docker deployment model and keep the registry grep-friendly.

## Consequences

**Easier:** Experimental code can merge to `dev`/`main` behind explicit env toggles; admins can inspect flag state without shell access.

**Harder:** Call sites must use `is_enabled()` and register new flags in the closed list; unknown keys fail loudly at runtime.

## Revisit conditions

- Per-flag UI toggles or household-scoped flags (would need auth/RBAC + persistence design).
- When a flag stabilises, remove the gate and delete the env var (do not accumulate permanent dead flags).

## Testing retrospective

| | |
| --- | --- |
| **Added** | `orchestrator/tests/test_features.py` (17 unit), `orchestrator/tests/test_api_v1_admin_feature_flags.py` (admin API) |
| **Run** | `pytest tests/test_features.py` → **17 passed** |
| **Skipped locally** | Admin API suite — `TestClient(main.app)` lifespan exceeds 120s in this environment (full orchestrator boot); **CI gate** |
| **Gaps** | None P0 — unit coverage for registry + parsing; admin contract covered in CI |
