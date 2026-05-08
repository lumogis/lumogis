# Self-hosted remediation — consolidation review

**Slug:** `self_hosted_remediation_consolidation_review`  
**Date:** 2026-04-25  
**Scope:** Phases 0–4 completed chunks (audit vocabulary, boundary hygiene, unified tool catalog, execution bridge, LLM/OOP wiring behind flag, `/api/v1/me/tools` + Web read-only view). No new implementation in this pass.

**Later closeout:** For the full Phase 4 household-control surface (tools, LLM providers, notifications, admin diagnostics, dev workflow) see [`phase-4-household-control-surface-closeout-review.md`](phase-4-household-control-surface-closeout-review.md) (2026-04-26). **Durable ADR:** [`ADR 028`](../decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md) summarises Phase 0–4 architectural decisions in one place.

---

## Executive summary

The repository state matches the intent of [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) for the chunks named in the task: terminology/plugin docs and connector-permissions wording (Phase 0), route-layer adapter hygiene + CI gates (Phase 1), read-only unified catalog + vocabulary (Phase 2), `capability_http` + `ToolExecutor` + shared graph HTTP helper without chat hot-path change (Phase 3A), flag-default-off LLM catalog merge + ContextVar-scoped OOP routes (Phase 3B), and Phase 4 read-only `GET /api/v1/me/tools` with OpenAPI snapshot coverage plus Lumogis Web **Tools & capabilities** at `/me/tools-capabilities`.

[`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) §5 contained **stale “current state” bullets** (pre-Phase 2/3 wording). Those are corrected in-plan so the living document does not contradict shipped code.

Known limitations called out in the plan remain accurate: **`permission_mode` is largely not DB-resolved**, the **catalog is mostly global per instance** (with `user_id` reserved for future filtering in `prepare_llm_tools_for_request`), **OOP audit fan-in is structured logging / `ToolAuditEnvelope`, not `audit_log` rows**, **`npm run codegen:check` requires a live orchestrator** at `LUMOGIS_OPENAPI_URL`, and **hosted/cloud/marketplace posture stays deferred** — the plan’s executive reframing still binds near-term work to **self-hosted household** use, not SaaS multi-tenant.

**Recommended next chunk:** `me_llm_providers_facade_and_view` (see §7). No blocker was found that should preempt another Phase 4 household-control façade.

---

## Completed work matrix

| Phase / chunk | Slug (as in plan) | Verified in repo |
| --- | --- | --- |
| 0 / 1 | Audit vocabulary + backlog mapping; connector_permissions terminology | Plan + `docs/architecture/plugin-imports.md`, `tool-vocabulary.md`; `connector_permissions` uses action-registry wording (see plan Chunk 1 status). |
| 1 / 2 | Boundary hygiene | No `adapters.*` imports under `orchestrator/routes/` (`rg`); `signal_source_detection` pattern per Chunk 2; tests below. |
| 2 / 3 | Unified tool catalog (read-only) | `services/unified_tools.py` (`ToolCatalog`, `ToolCatalogEntry`, deterministic ordering); Phase 2 tests; vocabulary doc. |
| 3A / 4 | Execution bridge | `capability_http.py`, `execution.py` (`ToolExecutor`, `PermissionCheck`, `ToolAuditEnvelope`); graph proxy uses shared HTTP helper; chat path unchanged until flag. |
| 3B / 5 | LLM capability wiring | `LUMOGIS_TOOL_CATALOG_ENABLED` default false (`config.py`); `loop.py` uses `prepare_llm_tools_for_request` / `finish_llm_tools_request`; `run_tool` tries `ToolSpec` before OOP; `test_mcp_manifest_unchanged.py` guards MCP manifest. |
| 4 | `me_tools_catalog_facade` | `GET /api/v1/me/tools` on `routes/me.py`; `me_tools_catalog.py`; OpenAPI + `REQUIRED_V1_PATHS`. |
| 4 | `lumogis_web_tools_catalog_view` | `fetchMeTools` → `/api/v1/me/tools`; `MeToolsCapabilitiesView.tsx`; Vitest `MeToolsCapabilitiesView.test.tsx`. |

---

## Boundary verification

| Check | Result |
| --- | --- |
| `orchestrator/routes/*` → `adapters.*` | **None found** (`rg 'from adapters\|import adapters' orchestrator/routes`). |
| Graph plugin `config` exception | **Documented** in [`plugin-imports.md`](plugin-imports.md) and cross-linked from `ARCHITECTURE.md` (Phase 0). |
| `loop.py` catalog path | Imports `prepare_llm_tools_for_request` / `finish_llm_tools_request`; on failure or flag off, falls back to `TOOLS`; `finally` clears OOP context token. |
| `mcp_server.py` vs Phase 3B | **No references** to `LUMOGIS_TOOL_CATALOG` / unified catalog in `mcp_server.py`; `test_mcp_manifest_unchanged.py` pins manifest/tool set stability. |
| `run_tool` vs OOP | **`ToolSpec` lookup first**; OOP only when `spec is None` (`services/tools.py`). |
| OOP route context | **`ContextVar`** `OOP_TOOL_ROUTES` set in `prepare_llm_tools_for_request`, reset in `finish_llm_tools_request` (`unified_tools.py`); isolation tests exercise concurrency/stream paths. |

---

## Test coverage inventory

Grouped by concern. **Adequate** = CI-grade regression + intent clear; **thin** = smoke or narrow assertion; **missing** = no dedicated test or only indirect coverage.

| Concern | Tests (representative) | Assessment |
| --- | --- | --- |
| Boundary hygiene | `test_routes_no_adapter_imports.py` | **Adequate** (AST scan of route package). |
| Vendored model drift | `test_vendored_models_in_sync.py` | **Adequate**. |
| Manifest validation | `test_capability_manifest_validation.py` | **Adequate** (good/bad fixtures). |
| Catalog | `test_tool_catalog_includes_core_tools.py`, `test_tool_catalog_includes_plugin_tools.py`, `test_tool_catalog_mcp_vs_llm.py`, `test_tool_catalog_capability_discovery.py` | **Adequate**. |
| Capability HTTP bridge | `test_capability_http_tool_proxy.py` | **Adequate** for proxy/HTTP shape; formal invoke contract across all capabilities remains a product gap (see risks). |
| Executor / permission / audit envelope | `test_tool_executor_permission_check.py`, `test_tool_executor_audit.py` | **Adequate** for unit-level executor; **thin** for end-to-end DB audit (by design deferred). |
| LLM / OOP wiring | `test_loop_uses_unified_catalog.py`, `test_llm_capability_tool_dispatch.py` | **Adequate**. |
| Request isolation / stream cleanup | `test_loop_oop_tool_isolation.py` | **Adequate**. |
| `GET /api/v1/me/tools` | `test_api_v1_me_tools.py` | **Adequate** (auth + shape/snapshot-oriented checks per file). |
| Web tools/capabilities page | `tests/features/me/MeToolsCapabilitiesView.test.tsx` | **Adequate** for read-only UI smoke; not a substitute for E2E. |
| MCP unchanged | `test_mcp_manifest_unchanged.py` | **Adequate** regression guard for manifest/tool identity. |

No new tests were added in this review pass.

---

## Web / API contract verification

| Check | Result |
| --- | --- |
| OpenAPI snapshot | `/api/v1/me/tools` present in `clients/lumogis-web/openapi.snapshot.json` (`rg '/api/v1/me/tools'`). |
| Web client URL | `fetchMeTools` uses `GET /api/v1/me/tools` (`clients/lumogis-web/src/api/meTools.ts`). |
| No raw schemas / handlers / bearer | Backend façade `me_tools_catalog.py` strips descriptions to plain text from schema `description` only; response models are curated DTOs. Web renders labels, transport, tier, availability, connector/action **names**, `capability_id` as observational “Service” — not execution handles or tokens. |
| Read-only | Route docstring and UI copy state no execution; no POST/invoke controls in `MeToolsCapabilitiesView.tsx`. |
| Codegen / check | `npm run codegen` consumes committed **snapshot**; `npm run codegen:check` / `make web-codegen-check` **fetches live** `openapi.json` (default `http://localhost:8000/openapi.json`) and compares canonicalised JSON to snapshot — **requires running orchestrator** (`clients/lumogis-web/scripts/codegen.mjs`). |

---

## Risk register

| Risk | Severity | Blocks next Phase 4 façade work? | Suggested follow-up slug |
| --- | --- | --- | --- |
| DB audit fan-in for OOP tools not in `audit_log` (structured log / envelope only) | Medium | **No** | `audit_log_oop_fanin` |
| Catalog `permission_mode` not fully per-user / DB-resolved | Medium | **No** | `tool_catalog_permission_resolution` |
| OOP invoke path still graph-shaped `{base}/tools/{name}` convention | Medium | **No** (document when adding capabilities) | `capability_invoke_contract_v1` |
| Capability manifest → HTTP invoke contract not fully formalised for arbitrary services | Medium | **No** | `capability_invoke_contract_v1` |
| `LUMOGIS_TOOL_CATALOG_ENABLED` default **false** — capability tools invisible to LLM unless enabled | Low–medium (operational) | **No** | `tool_catalog_flag_rollout` (docs + ops) |
| Local `make test` / `make test-integration` use `$(PYTHON)` (default `python3`); compose still uses in-container `python` after `pip install` dev deps | Low | **No** | Mitigated by `dev_test_workflow_hardening` (2026-04-26) |
| `codegen:check` needs live orchestrator — CI/local friction | Low | **No** | `openapi_check_offline_or_mock` (optional) |
| KG / capability HTTP auth: optional bearer / legacy-compatible paths | Medium (security posture) | **No** | `kg_capability_auth_hardening` |

---

## Recommended next chunk

**At review date:** `me_llm_providers_facade_and_view` (now shipped per plan §6).

**Suggested next (post–notifications):** The next Phase 4 thin façade from the plan (e.g. admin diagnostics / `admin_shell_api_v1` slices) per Web migration priority — see [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) §3 Phase 4 step 4.

**Rationale (historical):** Phase 4 lists curated `/api/v1/me/*` summaries as household-control needs independent of the tool catalog.

**Override condition (not met):** A serious boundary regression (e.g. routes importing adapters) or broken flag-off parity would block — neither was observed.

---

## Follow-up backlog additions (documentation-only)

Suggested slugs to track outside this file (e.g. portfolio or future `/create-plan`):

- `tool_catalog_permission_resolution` — resolve `permission_mode` from `connector_permissions` / Ask-Do in catalog + `/me/tools`.
- `audit_log_oop_fanin` — persist OOP tool executions into `audit_log` with correlation ids.
- `capability_invoke_contract_v1` — document and test standard `{base}/tools/{name}` + headers/envelope beyond graph.
- `me_llm_providers_facade_and_view` — shipped (see plan §6).
- `me_notifications_facade_and_view` — shipped (see plan §6 Chunk 6; **Addendum** at end of this file).
- **Plan hygiene:** keep Chunk 6 “files likely touched” aligned with **as-shipped** paths (`routes/me.py` for `/me/tools`, not only hypothetical `routes/api_v1/me/tools.py`).

---

## Verification commands run

Run from the repository root (paths relative to root):

- `rg 'from adapters|import adapters' orchestrator/routes` — no matches.
- `rg '/api/v1/me/tools' clients/lumogis-web/openapi.snapshot.json` — present.
- `rg 'LUMOGIS_TOOL_CATALOG|prepare_llm_tools|unified_tools' orchestrator/mcp_server.py` — no matches.
- `docker compose run --rm -w /project/orchestrator orchestrator sh -c "pip install -q -r requirements-dev.txt && python -m pytest tests/test_routes_no_adapter_imports.py tests/test_api_v1_me_tools.py tests/test_mcp_manifest_unchanged.py tests/test_loop_uses_unified_catalog.py -q"` — **30 passed** (includes subtests/collected tests from those files).

Local `python3 -m pytest` without a venv failed (`No module named pytest`); `make compose-test` or the full `pip install … && python -m pytest` pattern above is the supported verification path per plan Chunk 5 closeout.

---

## Addendum (2026-04-25)

After this review, **`me_notifications_facade_and_view`** shipped: `GET /api/v1/me/notifications` (`services/me_notifications.py`, `routes/me.py`) and Lumogis Web **Settings → Notifications** at `/me/notifications` (`fetchMeNotifications`, `MeNotificationsView`). The risk register and deferred follow-up slugs above are **unchanged**; see [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) §6 Chunk 6 for the consolidated deferred-follow-up table (including `me_notifications_edit_facade_or_connector_link` and codegen-check / LLM-runtime / credential-UX items).

## Addendum (2026-04-26) — `dev_test_workflow_hardening`

Contributor test workflow docs and `Makefile` were aligned: `PYTHON ?= python3` for local test targets, canonical **Docker** path `make compose-test` (dev deps installed in container), removal of **non-existent `make setup`** references, and explicit notes on `codegen:check` requiring a **live** orchestrator. `openapi_check_offline_or_mock` remains an optional follow-up. See `CONTRIBUTING.md` and `docs/dev-cheatsheet.md`.

## Addendum (2026-04-26) — `admin_diagnostics_v1_facade`

**`GET /api/v1/admin/diagnostics`** shipped (`services/admin_diagnostics.py`, extended `routes/admin_diagnostics.py`): admin-only curated Core/store/capability/tool summary; Lumogis Web **Admin → Diagnostics** consumes it via `fetchAdminDiagnostics`. The risk register and deferred follow-up table in this file are unchanged.
