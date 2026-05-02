# Phase 4 household-control surface — closeout review

**Slug:** `phase_4_household_control_surface_closeout_review`  
**Date:** 2026-04-26  
**Scope:** Read-only verification of shipped Phase 4 Web/Core household-control facades listed below. **No new implementation** in this pass.

**Sources reviewed:** [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) (§3 Phase 4, Chunk 6 status), [`self-hosted-remediation-consolidation-review.md`](self-hosted-remediation-consolidation-review.md), **ADR 028** ([`docs/decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md`](../decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md) — durable decision record for Phases 0–4), [`tool-vocabulary.md`](tool-vocabulary.md), [`plugin-imports.md`](plugin-imports.md), `ARCHITECTURE.md`, `clients/lumogis-web/README.md`, `CONTRIBUTING.md`, `docs/dev-cheatsheet.md`, `Makefile`, and the backend/Web files cited in the review task.

---

## Executive summary

The four **household-control** JSON facades and their Lumogis Web views are **present, coherent, and aligned** with the remediation plan’s Phase 4 intent: **thin routes**, **curated Pydantic DTOs**, **read-only** behaviour, **no secret material** on the wire, and **no changes** to MCP, chat hot-path semantics when the tool-catalog flag is off, or notification delivery.

The plan’s **near-term framing remains self-hosted household AI**; **Phase 5** is documented as a **separate** optional-capability / premium-boundaries phase and is **not** implied to have started in code.

**Small doc fix applied:** Phase 4 “in scope” bullet for notifications now references **`GET /api/v1/me/notifications`** (replacing a stale `notification-targets` path).

**Recommended next step:** **A — Enter Phase 5 with a narrow planning-only chunk** (scope and boundaries first, then implementation). **No Phase 4 blocker** was found that should delay that planning pass; remaining gaps are **known follow-ups** (audit fan-in, permission resolution, flag rollout, etc.) and are **non-blocking** for starting Phase 5 **planning**. **Phase 5 planning started (2026-04-26):** [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md).

---

## Completed Phase 4 matrix

| Chunk | API | Web route | Plan status (Chunk 6) |
| --- | --- | --- | --- |
| `me_tools_catalog_facade` | `GET /api/v1/me/tools` | `/me/tools-capabilities` | Documented shipped |
| `me_llm_providers_facade_and_view` | `GET /api/v1/me/llm-providers` | `/me/llm-providers` | Documented shipped |
| `me_notifications_facade_and_view` | `GET /api/v1/me/notifications` | `/me/notifications` | Documented shipped |
| `admin_diagnostics_v1_facade` | `GET /api/v1/admin/diagnostics` (+ existing fingerprint GET) | `/admin/diagnostics` | Documented shipped |
| `dev_test_workflow_hardening` | — | — | Documented (`PYTHON ?= python3`, compose/docs/codegen notes) |

---

## API surface verification

### OpenAPI snapshot

All four paths are present in `clients/lumogis-web/openapi.snapshot.json` (verified via repository search):

- `/api/v1/me/tools`
- `/api/v1/me/llm-providers`
- `/api/v1/me/notifications`
- `/api/v1/admin/diagnostics`

### Per-endpoint checks

| Endpoint | Thin route | DTO | Auth | Read-only | Secrets / execution |
| --- | --- | --- | --- | --- | --- |
| `GET /api/v1/me/tools` | Delegates to `me_tools_catalog.build_me_tools_response` | `MeToolsResponse` | `require_user` (router deps) | GET only | Docstrings + tests assert no raw schemas / no invoke |
| `GET /api/v1/me/llm-providers` | Delegates to `me_llm_providers` service | `MeLlmProvidersResponse` | `require_user` | GET only | Metadata / tiers; no ciphertext or env values in JSON |
| `GET /api/v1/me/notifications` | Delegates to `me_notifications` service | `MeNotificationsResponse` | `require_user` | GET only | No tokens/payloads; does not call send/delivery |
| `GET /api/v1/admin/diagnostics` | Delegates to `admin_diagnostics.build_admin_diagnostics_response` | `AdminDiagnosticsResponse` | `require_admin` on router | GET only | Pings + registry + catalog summary; no env dumps |

**LLM routing:** These endpoints do not alter provider selection or chat behaviour; they are observational only.

---

## Web surface verification

### Routing and nav

| View | `App.tsx` route | Nav |
| --- | --- | --- |
| Tools & capabilities | `/me/tools-capabilities` | `MeNav` |
| LLM providers | `/me/llm-providers` | `MeNav` |
| Notifications | `/me/notifications` | `MeNav` |
| Admin diagnostics | `/admin/diagnostics` | `AdminNav` (admin shell) |

### Behaviour (spot-check + tests)

- **Read-only:** Views use `useQuery` + API helpers; no execute/send/save/reveal patterns in Vitest for these surfaces (e.g. `MeToolsCapabilitiesView`, `MeNotificationsView`, `AdminDiagnosticsView` assert absence of dangerous buttons where tested).
- **API helpers:** `fetchMeTools`, `fetchMeLlmProviders`, `fetchMeNotifications`, `fetchAdminDiagnostics` — thin `getJson` wrappers.
- **No backend logic duplication:** Presentation only; sorting/filtering is cosmetic.

**Admin shell:** Non-admins are redirected at `AdminPage` (`role !== "admin"` → `/chat`); API returns 403 for non-admin diagnostics consistent with `require_admin`.

---

## Safety boundary verification

| Check | Result |
| --- | --- |
| `loop.py` uses `prepare_llm_tools_for_request` / `finish_llm_tools_request` | Yes; wrapped in try/finally |
| Catalog flag default **false** | `config.get_tool_catalog_enabled()` — only true for `1`/`true`/`yes` |
| When flag false, `prepare_llm_tools_for_request` returns `TOOLS` | `unified_tools.py` `_catalog_flag()` gates OOP merge |
| `run_tool` prefers in-process `ToolSpec` | `spec = next(...TOOL_SPECS)`; OOP only when `spec is None` |
| MCP server vs unified catalog | **No** matches for `LUMOGIS_TOOL_CATALOG`, `prepare_llm_tools`, `unified_tools` in `mcp_server.py` (rg) |
| Routes → adapters | **No** `from adapters` / `import adapters` under `orchestrator/routes` (rg) |
| Phase 4 GET facades → DB migrations | No new migrations required for these read-only endpoints; schema unchanged by this surface set |
| Credential / notification / capability **execution** semantics | Not altered by these routes (additive GETs only) |

---

## Test coverage inventory

| Group | Tests | Assessment |
| --- | --- | --- |
| `GET /api/v1/me/tools` | `test_api_v1_me_tools.py` | **Adequate** (auth, shape, safety, ordering, capability row metadata) |
| `GET /api/v1/me/llm-providers` | `test_api_v1_me_llm_providers.py` | **Adequate** |
| `GET /api/v1/me/notifications` | `test_api_v1_me_notifications.py` | **Adequate** |
| `GET /api/v1/admin/diagnostics` | `test_api_v1_admin_diagnostics.py` + `test_admin_diagnostics_routes.py` (fingerprint) | **Adequate** |
| Web — tools / LLM / notifications | `MeToolsCapabilitiesView.test.tsx`, `MeLlmProvidersView.test.tsx`, `MeNotificationsView.test.tsx` | **Adequate** (smoke + safety hints) |
| Web — admin diagnostics | `AdminDiagnosticsView.test.tsx` | **Adequate** |
| OpenAPI snapshot | `test_api_v1_openapi_snapshot.py` | **Adequate** |
| MCP unchanged | `test_mcp_manifest_unchanged.py` (existing) | **Adequate** guard |
| OOP isolation / Phase 3B | `test_loop_oop_tool_isolation.py`, `test_loop_uses_unified_catalog.py`, `test_llm_capability_tool_dispatch.py` | **Adequate** |
| Dev workflow | Makefile `PYTHON ?= python3`; `CONTRIBUTING.md` / `dev-cheatsheet.md` | **Adequate** (docs + convention) |

**High-value optional gap (not added in this review):** Cross-role E2E proving **403 + empty admin** for a member on `/admin/diagnostics` in a real browser (Playwright) — current coverage is unit/integration style.

---

## OpenAPI / codegen state

- **Snapshot:** Includes all four household-control GET paths above.
- **Generated TS:** `clients/lumogis-web/.gitignore` ignores `src/api/generated/` — intentional; helpers use hand-written types where used.
- **Docs:** README / dev-cheatsheet describe **`npm run codegen:check`** / live orchestrator requirement.
- **Follow-up:** `openapi_check_offline_or_mock` remains valid if CI should enforce snapshot without a running Core.

---

## Updated risk register

| Risk | Severity | Blocks Phase 5? | Follow-up slug |
| --- | --- | --- | --- |
| OOP tool audit not fan-in to `audit_log` (structured / envelope only) | Medium | **No** | `audit_log_oop_fanin` |
| Tool catalog `permission_mode` not fully DB-resolved per user | Medium | **No** | `tool_catalog_permission_resolution` |
| LLM provider façade metadata-only (no runtime decrypt / health for display) | Low–medium | **No** | `me_llm_providers_runtime_decrypt_health` |
| Notification edit/write façade deferred (Connectors / future) | Low | **No** | `me_notifications_edit_facade_or_connector_link` |
| Household/system credential admin UX beyond thin facades | Medium | **No** | `household_system_credential_admin_ux` |
| Capability invoke contract still graph-shaped / not fully formalised | Medium | **No** | `capability_invoke_contract_v1` |
| `LUMOGIS_TOOL_CATALOG_ENABLED` default **false** — ops/docs rollout | Low–medium | **No** | `tool_catalog_flag_rollout` |
| KG / capability HTTP auth hardening | Medium | **No** | `kg_capability_auth_hardening` |
| Admin diagnostics **`ok` vs `degraded`** — optional stores (e.g. graph) vs required deps classification could be clearer for operators | Low | **No** | `admin_diagnostics_dependency_semantics` (suggested) |
| OpenAPI drift check requires live Core | Low | **No** | `openapi_check_offline_or_mock` |
| Phase 4 exit item: portfolio `/verify-plan` for every chunk | Process | **No** | Skill workflow / portfolio hygiene |

---

## Recommended next step

**A. Enter Phase 5 with a narrow planning-only chunk.**

**Rationale:** Phase 4 household-control surfaces are **shipped, tested, and documented**; safety boundaries (MCP isolation, flag-off parity, `run_tool` ordering, route adapter hygiene) **match intent**. Remaining risks are **explicit follow-ups**, not regressions introduced by these facades. Phase 5 (optional capabilities / household premium boundaries per plan §299+) should begin with **written scope and non-goals** before code, because it touches **trust boundaries** and optional services.

**Override:** Would apply if a **security or parity regression** were found (e.g. MCP coupling to the unified catalog, or flag-off behaviour change) — **none observed** in this review.

---

## Follow-up backlog (unchanged themes)

Consolidated from the remediation plan deferred table and consolidation review:

- `audit_log_oop_fanin`
- `tool_catalog_permission_resolution`
- `capability_invoke_contract_v1`
- `tool_catalog_flag_rollout`
- `kg_capability_auth_hardening`
- `openapi_check_offline_or_mock`
- `dev_venv_documentation_hardening` / compose-first testing (partially addressed by `dev_test_workflow_hardening`)
- `me_llm_providers_runtime_decrypt_health`
- `household_system_credential_admin_ux`
- `me_notifications_edit_facade_or_connector_link`

---

## Verification commands run (2026-04-26)

```bash
# OpenAPI paths (four GETs present in snapshot)
rg '/api/v1/me/tools|/api/v1/me/llm-providers|/api/v1/me/notifications|/api/v1/admin/diagnostics' clients/lumogis-web/openapi.snapshot.json

# MCP unchanged vs unified catalog
rg 'LUMOGIS_TOOL_CATALOG|prepare_llm_tools|unified_tools' orchestrator/mcp_server.py
# → no matches

# Route adapter hygiene
rg 'from adapters|import adapters' orchestrator/routes
# → no matches
```

**Backend (Docker):**

```bash
docker compose run --rm -w /project/orchestrator orchestrator sh -c \
  "pip install -q -r requirements-dev.txt && \
   python -m pytest tests/test_api_v1_me_tools.py tests/test_api_v1_me_llm_providers.py \
     tests/test_api_v1_me_notifications.py tests/test_api_v1_admin_diagnostics.py -q"
# → 33 passed
```

**Web:**

```bash
cd clients/lumogis-web && npm test -- --run tests/features/me tests/features/admin
# → 31 passed (12 files)

npm run lint && npm run build
# → success
```

---

## Files produced / changed by this review

| Action | File |
| --- | --- |
| **Created** | `docs/architecture/phase-4-household-control-surface-closeout-review.md` (this document) |
| **Updated** | `docs/architecture/lumogis-self-hosted-platform-remediation-plan.md` (notifications path bullet corrected) |
| **Updated** | `docs/architecture/self-hosted-remediation-consolidation-review.md` (pointer to this closeout) |

No application code or tests were changed as part of this review task.
