# ADR-159: BGE reranker admin UI in Lumogis Web (LUM-159)

**Status:** Finalised
**Created:** 2026-06-21
**Last updated:** 2026-07-10
**Decided by:** planned implementation
**Finalised by:** /verify-plan 2026-07-10 (Composer)
**Plan:** `.cursor/plans/archived/LUM-159-bge-reranker-web-ui.plan.md`
**Exploration:** `.cursor/explorations/archived/LUM-159-bge-reranker-web-ui.md`
**Draft mirror:** `.cursor/adrs/lum_159_reranker_web_ui.md`
**Builds on:** ADR-093 (LUM-466 debundle — BGE in base Core), `.cursor/adrs/lum_159_reranker_ui_decouple_from_462.md`

**Linear:** [LUM-159](https://linear.app/lumogis/issue/LUM-159/bge-reranker-ui-toggle-expose-retrieval-quality-setting-in-admin-ui)

## Context

The orchestrator already exposed a BGE reranker enable/disable toggle on the legacy dashboard (`PUT /settings`, `POST /settings/restart`). Lumogis Web admin had no equivalent. LUM-159 ports that flow with honest pending-vs-live state, Compose auto-restart polling, and a system-status health chip — decoupled from the LUM-462 optional-upgrades registry (ADR-093).

## Decision

1. **Backend read model** — `GET /settings` adds `reranker_backend_live` and `reranker_pending_restart` so the UI can show “change pending — restart to apply” without guessing env state.
2. **Admin route** — `/admin/search-settings` (`AdminSearchSettingsView`) with Save, Save & restart, health poll (`pollOrchestratorHealth` → `GET /health`), and manual-restart copy when `stack_control_reachable` is false.
3. **System status chip** — reranker active indicator on `AdminSystemStatusView` using live backend only; hidden while pending restart.
4. **Tests** — Vitest (`AdminSearchSettingsView`, `pollOrchestratorHealth`), pytest (`test_admin_settings_reranker_live.py`), Playwright read-only smoke (`admin_search_settings.spec.ts`).
5. **Coverage matrix** — row **2.2.16**; catalog ID in `scripts/feature-ids.json`.

## Alternatives considered

- **Wait for LUM-462 registry row** — rejected (ADR decoupling; BGE is base-profile, not Class A add-back).
- **Hot-reload without restart** — rejected; reranker loads at orchestrator startup via `config.get_reranker()`.

## Consequences

**Positive:** Household operators can toggle retrieval quality from Lumogis Web with the same semantics as the legacy dashboard; pending state is honest across Compose and Lumogis Server paths.

**Limits:** Lumogis Server still requires manual orchestrator restart until LUM-466 supervisor restart ships; Playwright smokes are read-only (no live restart in CI).

## Revisit conditions

- Native orchestrator restart on Lumogis Server (LUM-466) — enable Save & restart on Server persona.
- GGUF reranker path (LUM-461) — revisit toggle copy if torch leaves base profile.

## Testing retrospective

| Layer | Command | Result |
|-------|---------|--------|
| Vitest | `npm test -- --run tests/features/admin/AdminSearchSettingsView.test.tsx tests/features/admin/pollOrchestratorHealth.test.ts` | **7+ passed** |
| pytest | `pytest orchestrator/tests/test_admin_settings_reranker_live.py` | **4 passed** |
| Playwright | `admin_search_settings.spec.ts` on `lumogis-test` @ `http://127.0.0.1` | **4/4 passed** (2026-07-10) |
| Build gate | `npm run build` | **green** |

## Linear linkage (Product OS)

- **LUM-159** — scope complete; apply `/linear-update apply-closure LUM-159 --done`.
- Remove stale **LUM-462 blocks LUM-159** edge if still present in Linear.
