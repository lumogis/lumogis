# Phase 5 — Capability contract reference (first slice)

**Slug:** `phase_5_capability_contract_reference_plan`  
**Date:** 2026-04-26  
**Kind:** Architecture reference + **test-only verification** (`capability_contract_mock_service_test`). **No** production capability container or runtime behaviour change.

**Reads:** [`lumogis-self-hosted-platform-remediation-plan.md`](lumogis-self-hosted-platform-remediation-plan.md) §3 Phase 5, [**final scaffolding closeout** — `phase-5-final-capability-scaffolding-closeout-review.md`](phase-5-final-capability-scaffolding-closeout-review.md) (2026-04-26), [`phase-5-mock-contract-closeout-review.md`](phase-5-mock-contract-closeout-review.md) (incremental verification), [`phase-4-household-control-surface-closeout-review.md`](phase-4-household-control-surface-closeout-review.md), [`self-hosted-remediation-consolidation-review.md`](self-hosted-remediation-consolidation-review.md), [`tool-vocabulary.md`](tool-vocabulary.md), `ARCHITECTURE.md`, ADR [`010-ecosystem-plumbing.md`](../decisions/010-ecosystem-plumbing.md), ADR [`011-lumogis-graph-service-extraction.md`](../decisions/011-lumogis-graph-service-extraction.md), `services/lumogis-graph/README.md`, and the Core modules cited below.

---

## 1. Executive summary

**Phase 5 is not** building marketplace infrastructure, cloud multi-tenant isolation, premium SaaS billing, or a second production capability service.

**Phase 5 is:**

- **Confirming** `services/lumogis-graph/` as the **reference** optional capability: real HTTP surfaces, real `CapabilityManifest`, real household LAN trust (shared bearer + Core-owned identity).
- **Documenting** the **minimum contract** any *new* optional capability service should follow so Core discovery, health, catalog, diagnostics, and (when enabled) the generic HTTP executor path stay coherent.
- **Proving** with **tests only** (`httpx.MockTransport`, injected registry entries) that a **second, non-graph** capability can flow through the **same generic** registry → catalog → admin summary → `/api/v1/me/tools` → `CapabilityHttpToolProxy` / `ToolExecutor` path **without** graph-specific code in `loop.py` or new `query_graph`-style shims in `services/tools.py`.
- **Keeping** household LAN boundaries explicit (two directions):
  - **Core → capability** (tool invoke, webhook, etc.): Core sends a **per-service shared secret** as `Authorization: Bearer …`; the **capability verifies** it (e.g. timing-safe compare). `X-Lumogis-User` on these requests is **attribution only** — who Core believes the end-user is — not authentication of the caller to the capability.
  - **Capability → Core** (if the capability calls Core HTTP APIs): Core must **authenticate** the caller (session/JWT/service credential per route) before trusting any **capability-supplied** attribution headers; **`X-Lumogis-User` is never authentication** and must not be treated as proof of identity from an unauthenticated client.

**Programme status:** The scaffolding slice (mock tests, FU-1…FU-5, dev overlay) is **closed**; see [**final closeout**](phase-5-final-capability-scaffolding-closeout-review.md). Historical §5–§6 text below remains the contract reference.

---

## 2. Current reference capability shape (`lumogis-graph`)

### 2.1 Discovery and manifest

| Surface | Behaviour |
| --- | --- |
| **Base URL** | Declared in Core as `CAPABILITY_SERVICE_URLS` (ADR-010). Core fetches **`GET {base_url}/capabilities`** (hardcoded path in `CapabilityRegistry._fetch_one` — the manifest’s `capabilities_endpoint` must match in practice, typically `"/capabilities"`). |
| **`CapabilityManifest`** | Pydantic model in `orchestrator/models/capability.py` (vendored under `services/lumogis-graph/models/capability.py`). KG builds a static manifest in `services/lumogis-graph/routes/capabilities.py` (`id="lumogis-graph"`, `health_endpoint="/health"`, `tools[]` with six `graph.*` MCP-oriented names, `management_url` for `/mgm`, `min_core_version` pinned for rc/pre-release semver behaviour). |
| **`GET /health`** | KG exposes health; Core probes `base_url + manifest.health_endpoint` and sets `RegisteredService.healthy`. |

### 2.2 Core ↔ KG HTTP boundaries (ADR-011)

| Surface | Role |
| --- | --- |
| **`POST /webhook`** | Core (`graph_webhook_dispatcher`) sends `WebhookEnvelope` ( `orchestrator/models/webhook.py` ) with graph-specific `WebhookEvent` / payloads. **KG-specific** event set and writer routing. |
| **`POST /context`** | Chat hot path: bounded timeout (Core client ~40 ms; KG in-route budget 35 ms). Request/response shape is KG-specific (`query`, `user_id`, `max_fragments` → `fragments[]`). |
| **`POST /tools/query_graph`** | Fast path for the LLM `query_graph` **ToolSpec** when `GRAPH_MODE=service`. Uses same bearer discipline as `/webhook` (`GRAPH_WEBHOOK_SECRET` / `check_webhook_auth`). **KG-specific** tool name and response envelope (`{"output": ...}` on success). |

### 2.3 Core-side graph wiring (mode switch)

| Mechanism | Role |
| --- | --- |
| **`GRAPH_MODE=inprocess|service|disabled`** | `config.get_graph_mode()` (cached). Switches plugin registration, webhook registration, context injection, scheduler, and whether `main.py` calls `register_query_graph_proxy()`. |
| **`register_query_graph_proxy`** | `orchestrator/services/tools.py`: registers a **`query_graph` ToolSpec** whose handler POSTs via `graph_query_tool_proxy_call` → `post_capability_tool_invocation` to `{KG}/tools/query_graph`. This is the **reference for “ToolSpec + HTTP tool path”** but is **still graph-named and graph-pinned**. |
| **`CapabilityHttpToolProxy` / `post_capability_tool_invocation`** | `orchestrator/services/capability_http.py`: generic **`POST {base}/tools/{tool_name}`**, headers `X-Lumogis-User` + optional `Authorization: Bearer`. Default **`require_service_bearer=True`**; **`graph_query_tool_proxy_call`** sets `require_service_bearer=False` for legacy parity when `GRAPH_WEBHOOK_SECRET` is unset. |
| **`ToolExecutor.execute_capability_http`** | `orchestrator/services/execution.py`: permission check, health gate, bearer resolution, merges `user_id` into JSON body, calls `post_capability_tool_invocation`, maps outcomes to `ToolAuditEnvelope`. |
| **Phase 3B OOP catalog path** | `unified_tools.prepare_llm_tools_for_request` / `try_run_oop_capability_tool`: adds **non-colliding** capability tool names to the LLM list when `LUMOGIS_TOOL_CATALOG_ENABLED`; uses `config.get_capability_bearer_for_service(service_id)` (`LUMOGIS_CAPABILITY_BEARER_<SANITIZED_ID>`). **Generic** for any registered, healthy service with a configured bearer. On each OOP execution, `try_run_oop_capability_tool` emits **`oop_tool_audit` structlog** and fans in to **`audit_log`** via `persist_tool_audit_envelope` → `actions.audit.write_audit` (`tool.execute.capability` rows; safe JSON summaries only). |
| **Admin / household views** | `services/admin_diagnostics.py` summarises registry services (id, healthy, version, tool count). `services/me_tools_catalog.py` maps catalog entries to `GET /api/v1/me/tools` (availability + `why_not_available` for unhealthy capabilities). |

### 2.4 What should generalise vs stays KG-specific

**Should generalise (contract / Core code):**

- `CapabilityManifest` + `CapabilityRegistry` discovery + health probing.
- Generic **`POST /tools/{tool_name}`** invoke path with service bearer + `X-Lumogis-User` semantics (`capability_http`, `ToolExecutor`, Phase 3B OOP routing when flag on).
- Per-service bearer env (`get_capability_bearer_for_service`).
- Catalog rows for capability tools; admin diagnostics capability block; safe façade fields on `/api/v1/me/tools`.

**Stays KG-specific (by design until a second real service needs the same pattern):**

- `WebhookEnvelope` **event enum and payload models** (`webhook.py`).
- `graph_webhook_dispatcher` (hook → HTTP → KG).
- KG **`/context`** contract and timing budget tied to chat.
- **`/tools/query_graph`** body/response conventions and **`register_query_graph_proxy`** / `_query_graph_proxy_handler`.
- Falkor projection, reconcile, quality jobs, `/mgm`, KG MCP server under `services/lumogis-graph/kg_mcp/`.
- ADR-011 phase-1 note: KG may use **Core Postgres** for projection (`entities`, etc.) — **not** the target pattern for *new* capabilities (see §4 non-goals).

**Operator / launcher metadata:** The manifest field that exists today is **`management_url`** (`capability.py`). Narrative in the remediation plan may mention “ui_links”; there is **no** separate `ui_links` array on `CapabilityManifest` in the current Pydantic model — treat **`management_url`** as the shipped launcher hook unless/until the model gains structured links.

---

## 3. Generic vs KG-specific matrix

| Component | Current location | Generic now? | Should generalise in Phase 5? | Notes |
| --- | --- | --- | --- | --- |
| `CapabilityManifest` | `orchestrator/models/capability.py` | Yes | **Document only** (already shared) | KG fills instance-specific `tools`, `management_url`, `min_core_version`. |
| `CapabilityRegistry` | `orchestrator/services/capability_registry.py` | Yes | **Prove with mock** | Discovery uses **`GET {base}/capabilities`** only; manifest’s `capabilities_endpoint` is documentary unless Core is extended. |
| Health discovery | `RegisteredService.check_health`, `check_all_health[_sync]` | Yes | **Prove with mock** | Soft failure; never takes Core down. |
| `CapabilityHttpToolProxy` / `post_capability_tool_invocation` | `orchestrator/services/capability_http.py` | Yes (with KG legacy exception) | **Prove with mock** | `graph_query_tool_proxy_call` is the **intentional** non-generic bearer rule. |
| `ToolExecutor` | `orchestrator/services/execution.py` | Yes | **Prove with mock** | Audit is envelope/log today, not DB. |
| `WebhookEnvelope` | `orchestrator/models/webhook.py` | **No** (graph event set) | **Defer** | New capabilities need their own event models or a different ingress pattern. |
| `graph_webhook_dispatcher` | `orchestrator/services/graph_webhook_dispatcher.py` | **No** | **Defer** | KG-only hook wiring. |
| `/context` | KG `routes/context.py` + Core dispatcher | **No** | **Defer** | Chat-coupled; KG-specific payload. |
| `/tools/query_graph` | KG `routes/tools.py` + Core proxy registration | **No** | **Defer** | Reference *shape* (`/tools/{name}`) generalises; this endpoint’s **schema** does not. |
| Capability `ui_links` | *N/A in model* | **No field** | **Defer / clarify** | Use **`management_url`** today. |
| Admin diagnostics capability summary | `orchestrator/services/admin_diagnostics.py` | Yes | **Extend tests** | Already lists services + health + tool counts. |
| `/api/v1/me/tools` unavailable reasons | `unified_tools._entries_for_capability_service` + façade | Partially | **Extend tests** | Unhealthy → fixed string; bearer/permission granularity for OOP rows is still limited (see risks). |
| Vendored `webhook.py` / `capability.py` | Core + `services/lumogis-graph/models/` | **Shared schema** | **CI only** | `test_vendored_models_in_sync.py` / `make sync-vendored`. |

**Expected outcome (aligned with remediation intent):** `CapabilityManifest`, registry, health, capability HTTP proxy, executor, admin diagnostics, and catalog/façade **behaviour are already generic or generic enough** for a second capability **name**. **`graph_webhook_dispatcher`, reconcile/backfill, `/context`, and Falkor/graph storage remain KG-specific** until another capability needs the same ingress.

---

## 4. Minimal capability contract v1 (draft)

Minimal, **household LAN**, **no marketplace**. This is what a **new** optional service should implement so Core can discover it and (optionally) call tools through the generic path.

### 4.1 Required

- **`GET /capabilities`** (at the URL registered in `CAPABILITY_SERVICE_URLS`) returning JSON that validates as **`CapabilityManifest`** (`id`, `name`, `version`, `type`, `transport`, `tools[]`, `health_endpoint`, `capabilities_endpoint`, `min_core_version`, etc., per `capability.py`).
- **`GET {health_endpoint}`** relative to base, returning **HTTP 200** when the service is ready for probes (matches registry expectations).
- **Stable `id`** (dedup key), **semantic `name`/`version`**, **`tools[]`** with at least `name`, `description`, `input_schema` / `output_schema` (JSON Schema objects).
- **Optional `management_url`** when the service exposes an operator UI (absolute URL — ADR-011).

### 4.2 Optional (capability-dependent)

- **`POST /tools/{tool_name}`** — invoke surface for Core’s generic proxy (`post_capability_tool_invocation`). **Wire body (generic Core → capability):** `ToolExecutor.execute_capability_http` copies the LLM tool arguments into a **flat JSON object** and sets **`user_id`** to the authenticated Core user before POSTing. **Exception — KG `query_graph` bridge only:** `graph_query_tool_proxy_call` wraps the same args as `json_body={"input": payload}` so KG `QueryGraphRequest` validates; generic OOP tools and `post_capability_tool_invocation` for non-graph tools stay **flat**. Replacing a core tool name is reserved for dedicated bridges (e.g. `query_graph`); the mock must use a **non-colliding** name. <!-- SELF-REVIEW: explicit POST body + collision note (D1/D3). -->
- **Per-service bearer env (implementers):** `config.get_capability_bearer_for_service(service_id)` reads `LUMOGIS_CAPABILITY_BEARER_<SAFE>` where `SAFE` is `service_id` with each non-alphanumeric replaced by `_`, uppercased, truncated to 120 chars (e.g. `mock.echo` → `LUMOGIS_CAPABILITY_BEARER_MOCK_ECHO`). Empty/unset → generic OOP tools omitted from the LLM list (fail-closed). <!-- SELF-REVIEW: copied from config.py docstring so tests do not guess env keys (D3). -->
- **OOP tool naming:** `_collect_oop_eligible` **skips** any capability tool whose `name` is already in `TOOL_SPECS` or already claimed by another OOP route in the same request. Prefer a dedicated name such as `mock.echo_ping` (not `search_files`, `read_file`, `query_entity`, `query_graph`, …). <!-- SELF-REVIEW: collision rule from unified_tools._collect_oop_eligible (D1/D6). -->
- **`POST /webhook`** — only if the capability subscribes to Core lifecycle events; **do not** reuse KG `WebhookEnvelope` events without a coordinated contract extension.
- **`POST /context`** — only if the capability participates in chat context injection; expect strict latency budgets if on the hot path.
- **Admin/launcher** — Core surfaces `management_url` where applicable; no separate manifest field for multiple links today.

### 4.3 Required request / auth rules

- **Core → capability:** On outbound calls, Core supplies **`Authorization: Bearer <shared secret>`**; the **capability service authenticates** that token. Household LAN: shared secret is acceptable; compare with `hmac.compare_digest` on the service side per KG patterns.
- **Capability → Core:** If a capability calls Core’s HTTP APIs, **Core** performs normal route authentication. Any per-user attribution from the capability must only be accepted **after** that service identity is authenticated; do not trust raw `X-Lumogis-User` (or similar) from unauthenticated callers.
- **`X-Lumogis-User`**: **Attribution only** on Core-initiated capability requests (who Core is acting for). **Never** standalone proof of user identity; **never** replace capability-side bearer checks.
- **Ask/Do + connector for OOP execution:** Phase 3B routes set `connector` via `unified_tools._permission_connector_for(manifest)` — first entry in `manifest.permissions_required`, else `capability.{manifest.id}` — and `action_type` to the **tool name**. `ToolExecutor` calls `permissions.check_permission(connector, action_type, …)`. Tests for **`capability_contract_mock_service_test`** must either inject **`PermissionCheck`** / stub `check_permission` to allow the mock connector+action, or use a manifest **`permissions_required`** that matches a connector the test suite already grants. Otherwise “permission denied” is indistinguishable from a broken mock. <!-- SELF-REVIEW: closes D5/D6 gap — permission path was implicit. -->
- **mTLS:** optional / deferred (Phase 6 posture).

### 4.4 Required response / behaviour rules

- **Safe error envelopes:** avoid secrets and raw credential material in JSON errors; log discipline as in `capability_http` (truncated body in logs).
- **Timeouts + fail-soft:** proxy returns a **single user-facing unavailable string** on non-200 / transport errors (graph-aligned); executor maps to audit status. **Latency:** generic OOP uses `OOP_HTTP_TIMEOUT_S` (**10.0** s) in `try_run_oop_capability_tool`; the KG `query_graph` proxy path uses **2.5** s (`QUERY_GRAPH_PROXY_TIMEOUT_S`) — do not assume one global budget when reasoning about behaviour. <!-- SELF-REVIEW: D7 — budgets were unspecified. -->
- **Fail-closed:** generic OOP path requires **healthy service** + **non-empty per-service bearer** (`_collect_oop_eligible`); missing bearer → tools not offered to LLM.

### 4.5 Non-goals (new capabilities)

- **No direct Core DB credentials** by default; prefer Core HTTP APIs or capability-owned stores (ADR-011 phase-1 KG exception is legacy).
- **No marketplace install**, arbitrary third-party sandbox, or cloud tenant isolation in this phase.
- **No** requirement for signed manifests or public Plugin SDK.

---

## 5. First Phase 5 implementation slice (after this doc)

**Slug:** `capability_contract_mock_service_test`  
**Status (2026-04-26):** **Implemented** — `orchestrator/tests/test_capability_contract_mock_service.py` (MockTransport + injected registry; **no** production second service). Remaining Phase 5 exit items from the remediation plan (e.g. richer “why unavailable” semantics) are **not** all satisfied by this slice alone.

**Goal:** Use **tests only** to prove a **second, non-graph** capability can:

- Publish a valid **`CapabilityManifest`** (via `httpx.MockTransport` responses).
- Be discovered by **`CapabilityRegistry`**.
- Appear in **`ToolCatalog`** / `build_tool_catalog` (and thus in **`/api/v1/me/tools`** via the existing façade).
- Be summarised in **`/api/v1/admin/diagnostics`** (capability block).
- Expose one mock **`POST /tools/{name}`** endpoint (handled by the same mock transport).
- Be invoked through **`post_capability_tool_invocation`** and/or **`ToolExecutor.execute_capability_http`** and Phase 3B **`try_run_oop_capability_tool`** when the catalog flag is on.
- **Fail closed** when unhealthy, unauthenticated (no bearer), or permission-denied.
- **Not** add graph-specific code paths in `services/tools.py` or `loop.py`.
- **Not** pass Core DB credentials to the mock.

**Do not** add a real second container or production service.

**Likely techniques:** extend `test_tool_catalog_capability_discovery.py`, `test_api_v1_admin_diagnostics.py`, `test_llm_capability_tool_dispatch.py` / `test_loop_oop_tool_isolation.py`, and `test_capability_http_tool_proxy.py` **narrowly**; inject registry + env bearer `LUMOGIS_CAPABILITY_BEARER_*` consistent with `get_capability_bearer_for_service`.

**Permission testing:** At least one test should use an explicit **`PermissionCheck`** stub (or existing test hooks) so “permission denied” is asserted without depending on real `connector_permissions` rows for a novel connector id. <!-- SELF-REVIEW: D6 — makes negative path objective. -->

---

## 6. Acceptance criteria (`capability_contract_mock_service_test`)

- **No** `graph` string **special-casing** in the mock capability path (IDs/names like `mock-svc` / `echo` are fine).
- **No** new `services/tools.py` graph-specific additions beyond existing `query_graph` / proxy registration.
- **No** new DB migrations.
- **No** Core DB credential wiring for the mock capability.
- Mock tool appears in LLM/OOP path **only** when **healthy** and **bearer configured**; **missing bearer** → tool not eligible / invoke fail-closed per design.
- **`X-Lumogis-User`:** tests document that **unauthenticated** service requests must **not** trust the header; Core path only sets it on server-side `httpx` calls after Core auth (already the pattern — assert no client-spoofed path in tests).
- **`/api/v1/me/tools`** and **`/api/v1/admin/diagnostics`** expose **safe metadata only** (existing DTO rules).
- **All existing KG / graph tests** still pass.
- **Flag-off (Phase 3B):** With `LUMOGIS_TOOL_CATALOG_ENABLED=false`, the mock capability tool **must not** appear in the LLM tool list from `prepare_llm_tools_for_request`, **must not** be dispatchable via the request-scoped OOP route map (`try_run_oop_capability_tool` returns `None`), and **`run_tool("mock.echo_ping", …)`** must fail safely as an unknown tool **without** calling the mock HTTP endpoint — even if the mock service is healthy and a per-service bearer env var is set.

**Concrete test checklist (non-exhaustive):**

1. Registry discovers mock manifest from `MockTransport` **GET** `{base}/capabilities` and health **GET** `{base}/health` → `healthy=True` after probe (or inject `RegisteredService` + manifest consistent with existing catalog tests).
2. `build_tool_catalog` / `build_me_tools_response` contains the mock tool with `source`/`capability_id` expected; row **unavailable** when health false with `why_not_available` set.
3. `build_admin_diagnostics_response` lists the mock service id and **healthy** count matches injected state.
4. With `LUMOGIS_TOOL_CATALOG_ENABLED=true` and bearer set: `prepare_llm_tools_for_request` appends the mock OpenAI tool def; `try_run_oop_capability_tool` returns mock **200** body; assert **`X-Lumogis-User`** and **`Authorization`** on captured request.
5. **Negative:** bearer unset → tool def not appended (or executor `blocked_auth` / proxy `missing_service_auth` depending on code path).
6. **Negative:** `PermissionCheck` denies → JSON error payload from `try_run_oop_capability_tool` path (or equivalent) without HTTP call.
7. **Negative (service-side, optional):** unit test on mock handler that **401/403** when `Authorization` missing even if `X-Lumogis-User` is set — documents that attribution ≠ auth (can live in `test_capability_http_tool_proxy` style). <!-- SELF-REVIEW: D6 specificity. -->
8. **Flag-off:** `LUMOGIS_TOOL_CATALOG_ENABLED=false` → mock tool **not** in merged LLM defs, **no** OOP dispatch, `run_tool` → unknown tool, **no** POST to mock `/tools/…` (exercise explicitly in `capability_contract_mock_service_test`).

---

## 6b. Follow-up register (unified)

Single list for deferred work and open verification called out in this doc (see skill D8 — avoid scattering “next steps” only in risks).

| Id | Item | Owner / phase |
| --- | --- | --- |
| FU-1 | **`query_graph` HTTP body parity** — **fixed** (2026-04-26, `query_graph_body_parity_test_or_fix`): Core `graph_query_tool_proxy_call` sends `{"input": payload}`; generic capability tools remain flat | **Done** |
| FU-2 | **`audit_log` OOP fan-in** — **done** (2026-04-26, `audit_log_oop_fanin`): `persist_tool_audit_envelope` + `try_run_oop_capability_tool` | **Done** |
| FU-3 | **Catalog `permission_mode` / per-user Ask-Do** — **done** (2026-04-26, `tool_catalog_permission_resolution`): `build_tool_catalog_for_user` → `ask` / `do` / `blocked` / `unknown` via `get_connector_mode`; capability rows use `permissions_required[0]` or `capability.{id}` | **Done** |
| FU-4 | **Second capability compose proof** — **done** (2026-04-26, `phase_5_remaining_capability_hardening`): dev-only `services/lumogis-mock-capability` + `docker-compose.mock-capability.yml` (not default stack); tests in that tree | **Done** |
| FU-5 | **`capabilities_endpoint` vs discovery** — **done** (2026-04-26): v1 discovery hardcoded `GET {base}/capabilities`; field documentary; warning if manifest declares non-`/capabilities` | **Done** |

### Phase 5 self-hosted remainders — closure summary (2026-04-26)

Slug: **`phase_5_remaining_capability_hardening`**.

- **FU-3:** Read-model permission labels are accurate where `connector` + `is_write` are known; MCP rows stay `unknown`; lookup errors fail soft to `unknown`.
- **FU-5:** Contract is explicit in `CapabilityManifest` docs and `CapabilityRegistry` logs; no runtime discovery regression.
- **FU-4:** A minimal **non-product** second service exists for packaging smoke; operators opt in via the overlay compose file. **Not** a premium product capability; no Core secrets mounted.

**Phase 5 (self-hosted capability hardening)** is **sufficiently complete** for the stated programme: generic manifest path, mock + dev smoke coverage, audit fan-in, permission façade, and discovery contract clarity. Further work is **product-specific capabilities** or **Phase 6** deferrals (mTLS, marketplace, etc.) — out of scope here.

---

## 7. Deferred items (explicit)

Preserve deferrals from the remediation plan Phase 5 / Phase 6:

- **mTLS by default** for capability traffic.
- **Signed manifests** and marketplace distribution.
- **Marketplace installation** UX.
- **Public Plugin SDK**.
- **Third-party connector sandbox**.
- **External outbound webhook marketplace**.
- **Hosted/cloud multi-tenant** posture.
- **Full sync→async** migration of Core services/routes.
- **Production second capability service** (real container) — Phase 5 proves the pattern in CI only.

---

## 8. Risk register (blocking next test slice?)

| Risk | Blocks `capability_contract_mock_service_test`? |
| --- | --- |
| Capability invoke URL is still **conventionally** `{base}/tools/{name}` without a manifest-declared invoke base path | **No** — document-only for v1. |
| **KG auth** allows missing bearer on graph proxy path (`require_service_bearer=False`) | **No** — mock uses generic strict path; graph stays legacy. |
| **`audit_log` OOP fan-in** | **No** — **closed** (`audit_log_oop_fanin`, 2026-04-26). |
| **Catalog `permission_mode`** | **No** — **closed** (FU-3); façade uses `get_connector_mode` where connector is known. |
| **Capability service DB-access rule** is policy/docs unless integration enforces | **No** for mock slice; note for real services. |
| **Mock-only proof** vs compose | **Mitigated** — dev overlay `docker-compose.mock-capability.yml` + `lumogis-mock-capability` (FU-4). |
| **Vendored model drift** | **No** — already gated (`test_vendored_models_in_sync.py`). |
| **Core ↔ KG `query_graph` HTTP body shape:** **Aligned** — Core bridge wraps `{"input": payload}`; generic OOP POSTs stay flat (see §4.2). | **Closed** (FU-1). |

---

## 9. Recommended next prompt (`capability_contract_mock_service_test`)

Copy-paste for the implementation chunk:

```text
You are working in the Lumogis repository.

Task slug: capability_contract_mock_service_test

Goal: Add test-only coverage proving a second NON-graph capability can flow through the existing generic path: CapabilityRegistry discovery → ToolCatalog → GET /api/v1/me/tools (via me_tools_catalog) → GET /api/v1/admin/diagnostics capability summary → post_capability_tool_invocation / ToolExecutor.execute_capability_http → try_run_oop_capability_tool when LUMOGIS_TOOL_CATALOG_ENABLED is on.

Constraints:
- Do NOT add a production second service or Docker image.
- Use httpx.MockTransport (or existing test patterns) and inject CapabilityRegistry / env LUMOGIS_CAPABILITY_BEARER_* as needed.
- No new graph- or query_graph-specific code in services/tools.py or loop.py.
- No new DB migrations.
- Mock capability id/name must not require string special-casing on "graph" in Core.
- Cover fail-closed: unhealthy service, missing bearer, permission denied.
- Keep existing KG/graph tests passing.

Start from: docs/architecture/phase-5-capability-contract-reference-plan.md §5–§6 (acceptance + checklist), §4.2–§4.3 (wire formats, bearer env key, permission connector).
```

---

## 10. Answers to the planning questions

1. **Current reference contract (from `lumogis-graph`):** `CapabilityManifest` at **`GET /capabilities`**, **`GET /health`**, optional **`POST /webhook`** (`WebhookEnvelope`), optional **`POST /context`**, **`POST /tools/query_graph`** for the LLM proxy path; Core uses **`GRAPH_MODE`**, **`register_query_graph_proxy`**, **`graph_webhook_dispatcher`**, and generic **`CapabilityHttpToolProxy`** / **`ToolExecutor`** for HTTP tool POSTs.
2. **Already generic:** manifest schema, registry, health probes, `post_capability_tool_invocation` (minus graph legacy bearer), `ToolExecutor`, Phase 3B OOP route collection + `try_run_oop_capability_tool`, admin diagnostics capability section, capability rows in unified catalog and `/api/v1/me/tools`.
3. **KG-specific by design:** webhook event set, dispatcher, `/context`, graph storage/reconcile/quality, `/tools/query_graph` ToolSpec registration, KG MCP server, and ADR-011’s Core-DB projection coupling for KG.
4. **First mock second capability must prove:** end-to-end **generic** path works for a **non-graph** tool name with **strict** bearer and **no** new production service — and fails closed on health/auth/permission gaps.
5. **Must remain deferred:** mTLS-default, signed manifests, marketplace, public SDK, sandbox, cloud tenant isolation, real second service, full async migration — per §7.

---

## Self-Review Log

**Model:** Composer  
**Date:** 2026-04-26  
**Plan:** `phase-5-capability-contract-reference-plan` (architecture doc; not under *(maintainer-local only; not part of the tracked repository)*)  
**ADR consulted:** `docs/decisions/010-ecosystem-plumbing.md`, `docs/decisions/011-lumogis-graph-service-extraction.md` (cross-read; no topic row in *(maintainer-local only; not part of the tracked repository)* for this slug)

### Dimension findings

1. **Technical correctness & feasibility:** ⚠️ → **tightened** — added OOP **tool-name collision** rules, **bearer env sanitization**, and **`query_graph` body-shape** distinction so implementers do not mis-wire mocks.  
2. **Architecture & design quality:** ✅ — aligns with five-pillar overlay; no new routes required for mock slice.  
3. **Data contracts & completeness:** ⚠️ → **tightened** — documented flat JSON POST body from `ToolExecutor`, `get_capability_bearer_for_service` key algorithm, `_permission_connector_for` behaviour.  
4. **Error handling & resilience:** ✅ — plan already references fail-soft / fail-closed; checklist now names negative cases.  
5. **Security & trust:** ⚠️ → **tightened** — explicit **permission** path for OOP (`connector` / `action_type`); optional service-side test for bearer without trusting `X-Lumogis-User`.  
6. **Test coverage & quality:** ⚠️ → **tightened** — **§6 checklist** (7 items) + **PermissionCheck** stub guidance.  
7. **Performance & scalability:** ⚠️ → **tightened** — **10.0 s** vs **2.5 s** timeout note for generic OOP vs graph proxy.  
8. **Interoperability & future compatibility:** ⚠️ → **tightened** — **§6b Follow-up register** consolidates FU-1…FU-5; **no `FP-###` citations** (no portfolio row verified for this doc).

### Changes made

1. §4.2 — POST body contract, bearer env key formula, OOP naming/collision rules (with `<!-- SELF-REVIEW: … -->` comments).  
2. §4.3 — Ask/Do + `_permission_connector_for` / test implications.  
3. §4.4 — Timeout budget distinction (`OOP_HTTP_TIMEOUT_S` vs graph proxy).  
4. §5 — Permission-testing note for tests.  
5. §6 — Numbered concrete test checklist + optional service-side auth negative.  
6. New **§6b Follow-up register** table.  
7. §9 — Prompt points to §4.2–§4.3 and §6 checklist.

### Remaining uncertainties

- ~~**Live `GRAPH_MODE=service` `query_graph` request shape**~~ — FU-1 closed via wrapped body + tests (`query_graph_body_parity_test_or_fix`).  
- **`capabilities_endpoint` field** unused by `CapabilityRegistry._fetch_one` — document-only until Core changes (FU-5).

### Codebase context (for subsequent reviewers)

Lumogis Core (`orchestrator/`) is sync-first FastAPI + httpx; capability discovery is `CapabilityRegistry` with optional `MockTransport`. Phase 3B OOP tools are flag-gated (`LUMOGIS_TOOL_CATALOG_ENABLED`), request-scoped via `ContextVar`, and executed through `ToolExecutor.execute_capability_http` + `post_capability_tool_invocation`. KG remains the reference out-of-process service under `services/lumogis-graph/`. This plan lives in `docs/architecture/` as Phase 5 **planning**; implementation chunk is test-only.

### Process note

***(maintainer-local only; not part of the tracked repository)*** was **not** incremented (skill-managed index; no `create-plan` topic row for this file). For a formal review loop under `/review-plan`, consider a **`/create-plan`** artefact in *(maintainer-local only; not part of the tracked repository)* that references this doc, or accept architecture-doc self-review without index mutation.

---

**Self-review complete.** Plan updated in-place. Changes marked with `<!-- SELF-REVIEW: … -->` where noted. Reviews folder: *(maintainer-local only; not part of the tracked repository)* (for future `--critique` rounds).

**Next:** switch to your critique model and run: `/review-plan --critique <model>` (optional — if you promote this to *(maintainer-local only; not part of the tracked repository)*, use that file as the canonical plan for the skill’s index updates).
