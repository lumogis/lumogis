# Lumogis Reference Manual

<!-- markdownlint-disable MD013 -->

**Slug:** `lumogis_reference_manual`  
**Audience:** Household operators, curious readers, and contributors.  
**Scope:** Describes Lumogis **as of** consolidation after cross-device Lumogis Web Phase 0/1, Admin & Me shell closure, self-hosted architecture remediation Phases 0–5 (household surfaces + capability scaffolding), password-management foundation, admin user import/export UI, and extraction of the **parent** Web Phase 2 (mobile UX) plan.  
**Authority:** Prefer **closeout reviews**, **this manual §17**, and **ADRs** over stale plan prose when sources disagree.

Last reviewed: 2026-05-27
Verified against commit: **`6f3c5689b1f74e775ba66ea6dc734a77acfd6eba`** (`dev` — LUM-224 web Docker + LUM-250 `admin_users` 204; **ADR 043**) + **LUM-308** auto-RAG ship (**ADR 059**) + **LUM-124** memory-as-hint (**ADR 066**)

**Code cross-check (spot audit):** Key claims were traced to `orchestrator/config.py` (`get_tool_catalog_enabled`, `warmup_injection_sanitiser`, `get_tool_chain_cap`), `orchestrator/loop.py` + `orchestrator/services/unified_tools.py` (tool-list merge + teardown, **`ToolChainBudget`**), `orchestrator/services/injection_sanitiser.py` + `orchestrator/data/injection_patterns.yaml` (**`INJECTION_*`**, `<retrieved_chunk>` / **`lumogis_injected_context`** assembly), `orchestrator/services/capability_http.py` (`graph_query_tool_proxy_call` / `{"input": …}`), `orchestrator/services/execution.py` (`tool.execute.capability`), `orchestrator/routes/auth.py` (`REFRESH_COOKIE_PATH = "/api/v1/auth"`), `docker/caddy/Caddyfile` path table, `postgres/migrations/016-per-user-connector-permissions.sql` (per-user `connector_permissions`), `rg` for `from adapters` under `orchestrator/services/` and `orchestrator/routes/` (no matches), `docker-compose.yml` (no mock-capability service), and `clients/lumogis-web` routes under `/me/*` and `/admin/*`. **§19** frames extension work as **five practical families** (plus how lower-level pieces compose); it aligns with `ARCHITECTURE.md` / `CONTRIBUTING.md`, not a parallel architecture.

**Important — two different “Phase 4 / Phase 5” programmes:**

| Programme | “Phase 4” means | “Phase 5” means |
| --- | --- | --- |
| **Self-hosted architecture remediation** (household + capability scaffolding programme) | Household-control JSON façades + Web read-only views (e.g. `/api/v1/me/tools`, diagnostics) | Optional **capability** scaffolding (discovery, OOP tool bridge, mock service, audit fan-in) |
| **Cross-device Lumogis Web** (summarized in **§13–§17** and ADR 030) | **Web Push** + background approvals (not the same as remediation Phase 4) | **Capture-from-anywhere** (not the same as remediation Phase 5) |

This manual uses **remediation** vs **cross-device Web** explicitly to avoid confusion.

## How to read this manual

| Path | Who | Where to start |
| --- | --- | --- |
| **Household / operator** | You run Lumogis at home and want the big picture—what it is, what the parts do, how to deploy safely. | **§1–§3** (what / principles / components), **§13–§16** (Lumogis Web, APIs, deployment, security), **§17** (what is shipped vs planned). |
| **Technical contributor** | You are changing Core, Web, or integrations and need boundaries and patterns. | **§4–§5** (mental model, pillars), **§18–§19** (rules + extension families), **`ARCHITECTURE.md`** and **`CONTRIBUTING.md`** linked in **§21**. |
| **Roadmap / status** | You need to know what is done, what is next, and how “Phase 4/5” differs between programmes. | **Phase table** at the top of this page and **§17**. |

---

## 1. What Lumogis is

**In plain language:** Lumogis is a **self-hosted household AI platform**. The guiding idea is: **the AI comes to your data, not the other way around.** Your documents, notes, memory, and indexes stay on hardware you control (a home server, NAS, or workstation). When you ask a question, Lumogis **finds relevant context locally**, **assembles a composed prompt**, and sends that bundle to an LLM—**fully local** if you use Ollama, or **to a cloud provider** if you configure one. In the cloud case, **composed prompts and retrieved excerpts** (not your full local corpus) may leave the machine; see **§2** and **§8** for precision. **Connectors** (calendars, ntfy, LLM APIs, …) **call their configured external services by design** when you use those features. Lumogis is **not** a hosted SaaS, public marketplace, or multi-tenant cloud product; it is aimed first at a **family or household on a LAN**, with **Core** as the trust anchor for identity, policy, credentials, and audit.

**Examples:**

- Ask Lumogis to **find something** across your indexed notes and files (semantic search + retrieval).
- Let Lumogis **remember household context** across sessions (memory scopes: personal, shared, system—see [ADR 015](decisions/015-personal-shared-system-memory-scopes.md)).
- Use **tools and actions** with **Ask/Do** permissions and an **audit log** so destructive or external effects are visible and approvable.
- An **admin** can manage **users**, **connector credentials**, **tokens**, and **read-only diagnostics** from Lumogis Web; members use chat, search, and their own settings.

**Technical nutshell:** The **orchestrator** (FastAPI “Core”) owns HTTP APIs, business logic in `services/`, infrastructure via `adapters/`, optional in-process `plugins/`, **signals** and **actions**, Postgres metadata, Qdrant vectors, and optional **out-of-process capability** services (e.g. `lumogis-graph`). **Lumogis Web** is the **primary** first-party client for **multi-user household** chat, settings, and admin surfaces (same-origin with `/api/v1/*`). **LibreChat** is an **optional**, **legacy-compatible** OpenAI-style chat UI (compose profile)—useful for continuity with older setups; it is **not** the supported multi-user identity surface ([ADR 012](decisions/012-family-lan-multi-user.md)). Nothing here implies a scheduled removal unless a future release note says so.

---

## 2. Core principles

| Principle | Lay explanation | Technical note |
| --- | --- | --- |
| **Local-first / privacy-first** | Your archive stays on your machine. | Raw corpus, embeddings store, and audit DB are local; cloud use is opt-in per provider keys. |
| **Self-hosted by default** | You run the stack; there is no Lumogis cloud that holds your files. | Docker Compose; operator sets `.env`, Caddy, and secrets. |
| **Core owns policy and execution** | “What is allowed” is not reimplemented in random clients. | Auth, connector permissions, tool execution gates, audit—see [ADR 028](decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md). |
| **Users and household roles** | Operators vs members: admin can manage the instance; users use data scoped to them. | `admin` vs `user` roles, `users` table, `UserContext`—[ADR 012](decisions/012-family-lan-multi-user.md). |
| **Safe credential handling** | Passwords and API keys are not splashed in the UI after save. | Encrypted credential payloads, copy-once tokens where applicable—[ADR 018](decisions/018-per-user-connector-credentials.md), [027](decisions/027-credential_scopes_shared_system.md), [029](decisions/029-self-hosted-account-password-management.md). |
| **Auditability** | Important actions leave a trail. | Append-only `audit_log`; structured logging—[ADR 019](decisions/019-structured-audit-logging.md). |
| **Modularity** | Swap vector store, LLM, etc., via adapters and ports. | `ports/` + `config.py` factories—`ARCHITECTURE.md`. |
| **Optional capabilities** | Heavy or isolated features can run **beside** Core, not inside its DB. | HTTP manifest at `GET /capabilities`, bearer trust—[ADR 010](decisions/010-ecosystem-plumbing.md), **[ADR 002](decisions/002-graph-store-falkordb.md)** (graph Protocol + optional premium overlays). |
| **Bounded agents under Core policy** | Assistants stay permission-gated and audited—not unconstrained operators beside household controls. | Tool loops honour Ask/Do, audit, and catalog behaviour documented in shipped ADRs and extension guidance below. |
| **No full-corpus cloud upload by default** | Your entire indexed library is not bulk-uploaded to an LLM vendor. | If a **cloud LLM** is configured, **composed prompts + retrieved excerpts** may leave the machine; **Qdrant/Postgres and raw files stay local** unless another feature sends them out. **Connectors** intentionally reach **their** configured services. |

---

## 3. Main components at a glance

**ASCII diagram (default stack, conceptual):**

```text
                    ┌─────────────────────────────────────────┐
                    │  Browser / MCP clients / scripts        │
                    └───────────────┬─────────────────────────┘
                                    │ HTTP(S)
                    ┌───────────────▼─────────────────────────┐
                    │  Caddy (:80/443) — same-origin routing   │
                    │  / → lumogis-web (SPA)                   │
                    │  /api*, /events, /v1*, /mcp* → Core      │
                    └───────────────┬─────────────────────────┘
                                    │
         ┌──────────────────────────▼──────────────────────────┐
         │  Lumogis Core (orchestrator :8000)                   │
         │  services · actions · signals · routes · MCP mount   │
         └─┬────────────┬────────────┬────────────┬───────────┘
           │            │            │            │
     ┌─────▼─────┐ ┌────▼────┐ ┌─────▼─────┐ ┌──▼──────────┐
     │ Postgres  │ │ Qdrant  │ │ Ollama    │ │ Optional:   │
     │ metadata  │ │ vectors │ │ embed/LLM │ │ lumogis-    │
     │ audit…    │ │         │ │           │ │ graph, mock │
     └───────────┘ └─────────┘ └───────────┘ └─────────────┘

  Optional profile: LibreChat :3080 + Mongo (legacy-compatible chat UI; household multi-user control is Lumogis Web — ADR 012)
```

### Component table

| Component | What it does | Who uses it | Where it lives |
| --- | --- | --- | --- |
| **Lumogis Core (orchestrator)** | APIs, retrieval, memory, tools, auth, capability proxy, MCP server | All clients; operators | `orchestrator/` |
| **Lumogis Web** | First-party SPA: chat, search, approvals, Me/Admin settings | Household members + admin | `clients/lumogis-web/` |
| **LibreChat** (optional) | Legacy-compatible OpenAI-style chat UI | Operators who keep the profile enabled | Compose profile `librechat`; port **3080** |
| **Postgres** | Source of truth: users, credentials metadata, audit, signals, jobs… | Core only | `postgres/` migrations |
| **Qdrant** | Vector / hybrid search over chunks | Core | `docker/qdrant` image |
| **Ollama** | Local embeddings + chat models | Core | Compose service |
| **lumogis-graph** (optional) | Out-of-process KG: FalkorDB writes, tools, webhooks | Core invokes via HTTP; operators may use KG mgmt UI | `services/lumogis-graph/` |
| **lumogis-mock-capability** (optional) | Dev **second** capability for contract smoke | Developers | `services/lumogis-mock-capability/` + `docker-compose.mock-capability.yml` |
| **Caddy** | TLS, security headers, same-origin routing to SPA + Core | Everyone hitting `http(s)://host/` | `docker/caddy/Caddyfile` |
| **Connectors** | Registered ids (`ntfy`, `caldav`, LLM providers, …) + encrypted payloads | Core resolution + Web forms | `orchestrator/connectors/` + credential services |
| **MCP surface** | Streamable HTTP tools for external agents; optional **`lumogis-mcp`** stdio bridge for Cursor (`clients/lumogis-mcp/`, `make lumogis-cursor-install`) | MCP clients | `/mcp/` on Core |
| **Clients** | Any HTTP consumer of Core | Humans + automation | **Lumogis Web** (primary household UI), optional LibreChat, curl, MCP |

---

## 4. Mental model: Core, clients, connectors, tools, capabilities

Aligned with [`docs/architecture/tool-vocabulary.md`](architecture/tool-vocabulary.md). For each term: **easy explanation**, then **technical definition**.

| Term | Plain English | Technical |
| --- | --- | --- |
| **Core** | The brain on your box: policies, storage, and execution. | The orchestrator process and its adapters—Postgres/Qdrant/Ollama/etc. |
| **Client** | A program that talks to Core over HTTP and shows UX. | **Lumogis Web** (household + admin); optional **LibreChat** (chat-only legacy path); scripts; must stay thin—no direct DB. |
| **Connector** | A named channel for secrets/settings (ntfy, CalDAV, paperless-ngx, LLM provider, …). | Registered id in `connectors/registry.py`, encrypted rows in `user_connector_credentials`, tiers per [ADR 027](decisions/027-credential_scopes_shared_system.md); `ToolSpec.connector` links tools to permission checks. |
| **Credential** | The saved secret or config Lumogis uses on your behalf. | Encrypted payload + MultiFernet key rotation support in `_credential_internals`; never returned raw in Web façades. |
| **Tool** | Something the model may **call** with structured arguments. | `ToolSpec` + OpenAI-style definition; executed via `run_tool` / `ToolExecutor` with permission gates. |
| **Action** | A registered, audited operation (often with side effects). | `actions/` registry + executor + Ask/Do—[ADR 006](decisions/006-ask-do-safety-model.md). |
| **Signal** | Something the world pushed or that Core polled (feed, page, calendar…). | `SignalSource.poll()` → scoring → storage → `Event.SIGNAL_RECEIVED`. |
| **Routine** | Automation that can **elevate** Ask→Do after trusted approvals. | `services/routines.py`, threshold from env—paired with action audit. |
| **Plugin** | In-process Python extension in Core. | `plugins/<name>/`; hooks + optional `config` carve-out for first-party graph plugin—[`plugin-imports.md`](architecture/plugin-imports.md), [ADR 005](decisions/005-plugin-boundary.md). |
| **Capability** | Optional **separate** service Core discovers and may call. | HTTP `GET {base}/capabilities`, `GET {base}/health`, `POST {base}/tools/{name}`; bearer trust; **no** shared Core DB—[ADR 010](decisions/010-ecosystem-plumbing.md). |
| **MCP** | A **transport** exposing a small, curated tool set for agents. | FastMCP at `/mcp/`—not the full tool registry; [ADR 010](decisions/010-ecosystem-plumbing.md), [017](decisions/017-mcp-token-user-map.md). |

---

## 5. The five contributor pillars

Lumogis maps code to **five** pillars ([`ARCHITECTURE.md`](../ARCHITECTURE.md)):

1. **Services** — business logic (`orchestrator/services/`): search, memory, ingest, unified tool catalog builder, capability registry helpers, users, connector credentials, etc.
2. **Adapters** — one file per external system (`orchestrator/adapters/`): Qdrant, Postgres, Ollama, extractors, notifiers.
3. **Plugins** — optional in-process packages (`orchestrator/plugins/`).
4. **Signals** — monitors and scoring (`orchestrator/signals/`).
5. **Actions** — executable operations with audit (`orchestrator/actions/`).

**Tool catalog / execution** is a **cross-cutting overlay**, not a sixth pillar: `build_tool_catalog` / `build_tool_catalog_for_user` observe registries; `ToolExecutor` and `run_tool` enforce execution. **Capabilities** are **out-of-process** services, not plugins. **Routes** (`orchestrator/routes/`) should stay **thin** and call services—routes must not import adapters (CI-enforced).

**Examples in-repo:** `services/unified_tools.py` (catalog + `prepare_llm_tools_for_request` / `finish_llm_tools_request`), `services/execution.py` (`ToolExecutor`), `services/capability_http.py` (HTTP invoke + `graph_query_tool_proxy_call`), `services/me_tools_catalog.py` + `routes/me.py` (`GET /api/v1/me/tools` façade).

---

## 6. Users, roles, and household model

**Lay view:** A home instance has an **admin** (operator) and one or more **users** (family members). Everyone signs in through Lumogis Web when `AUTH_ENABLED=true`. The admin can reset passwords and manage household-level settings; members see only their data unless shared scopes apply.

**Technical view:**

- **`users` table** — accounts with argon2id password hashes, `role` (`admin` | `user`), optional `disabled`, monotonic **`token_version`** for access-JWT invalidation—[ADR 012](decisions/012-family-lan-multi-user.md), [029](decisions/029-self-hosted-account-password-management.md), [041](decisions/041-jwt-access-token-revocation-multi-device-sessions.md). Refresh rotation uses **`auth_sessions`** rows (migration **`023`**); the legacy **`refresh_token_jti`** column was dropped in migration **`037`** (LUM-244) once its downgrade window closed. Nullable **`onboarding_completed_at`** (migration **`025`**, LUM-165) records when the authenticated user dismissed the first-run onboarding gate in Lumogis Web. Nullable **`wow_dismissed_at`** (migration **`028`**, LUM-216) records when the user dismissed the first wow-moment card path (guided query + entity discovery on chat).
- **`auth_sessions` table** — one row per browser/device refresh session (distinct from chat **`sessions`** in migration **003**): hashed refresh token, **`family_id`** for OAuth-style reuse detection, optional **`device_label`** / fingerprint hashes—[ADR 041](decisions/041-jwt-access-token-revocation-multi-device-sessions.md).
- **Chat `sessions` table** (migration **003**, memory scopes in **013** — distinct from **`auth_sessions`** above): per-user conversation ledger with **`updated_at`** maintained by the shared **`set_updated_at`** trigger; migration **024** adds **`idx_sessions_user_updated_at (user_id, updated_at DESC)`** so **`memory.recent_sessions`** / MCP recency reads stay index-friendly as the table grows—[ADR 055](decisions/055-lum-209-sessions-recency-index.md).
- **Conversation history (LUM-162)** — Lumogis Web **`GET/DELETE /api/v1/conversations`** lists ended chats from **`sessions`** (after **`POST /session/end`** enqueues summarization). Continue seeds a **new** tab thread with summary context (slice 1) or verbatim transcript from **`web_conversations` / `web_messages`** (migration **027**, slice 2). **`POST /api/v1/conversations/{id}/messages`** and conversation detail responses accept optional **`source_refs`** (citation JSONB array) and **`action_proposal_id`** (nullable FK to **`action_proposals`**, migration **047**, LUM-395) so LUM-205 can render citations and proposal links from persisted rows; stable citation keys include **`document_id`**, **`chunk_index`**, **`quote`**, **`file_path`**. Cross-user proposal references return **404** `conversation_not_found`; unknown proposal ids return **422** `invalid_action_proposal`. Hard delete purges Postgres, Qdrant session points, and the personal FalkorDB **`Session`** node when graph mode is enabled; partial vector/graph failures return **`partial: true`** with an honest retry toast. **`purged_conversations`** tombstones (migration **034**) plus APScheduler **`purge_partial_sweep`** (LUM-416) reconcile stranded Qdrant/graph arms; published Session projection nodes are removed on delete (LUM-419). **`GET /api/v1/memory/recent`** (MCP/desktop consumers) returns recent **`sessions`** rows with **`ended_at`** mapped from **`updated_at`** (**LUM-418**); the Web sidebar uses **`/conversations`**, not **`/memory/recent`**. Lumogis Web **`threadStore.ts`** still holds per-tab ephemeral UI state until LUM-205 wires full server-side thread load.
- **Document library (LUM-160)** — Lumogis Web **`/documents`** lists ingested **`file_index`** rows via **`GET /api/v1/documents`** with derived status (`indexing` / `indexed` / `failed`). Personal documents can be **hard-deleted** (`DELETE /api/v1/documents/{id}`) across Postgres (including shared projections), Qdrant chunk points (`document_chunk_point_id`), and optional FalkorDB **`Document`** nodes; partial failures tombstone in **`purged_documents`** (migrations **032–033**) with UI retry (LUM-500/501). **Re-ingest** (`POST /api/v1/documents/{id}/reingest`, optional **`force`**) enqueues **`ingest_upload`** or **`ingest_watch_file`** batch work when the source file still exists on disk; missing sources return **409 `source_unavailable`**. **Push upload** (`POST /api/v1/ingest/upload`, 202) returns **`job_id`**; optional header **`X-Lumogis-Batch-Id`** groups multi-file batches. **Progress poll:** **`GET /api/v1/ingest/jobs/{job_id}`** and **`GET /api/v1/ingest/batches/{batch_id}`** (server returns `completed` / `failed` / `in_progress` only — the web client owns upload **`total`**). **SSE:** **`ingest_progress`** on **`GET /api/v1/events`** mirrors poll JSON; coarse list refresh still uses **`document_status_changed`** plus TanStack Query polling while any row is **`indexing`**. Stage bar on document detail is for **re-ingest** / **`in_flight_job_id`**; fresh multi-file upload progress lives in the library upload panel (LUM-511, [ADR 110](decisions/110-lum-511-ingest-job-progress-ux.md)). — [ADR 101](decisions/101-lum-160-document-library-ui.md), follow-ups [103](decisions/103-lum-160-document-purge-followups.md).
- **Household document sharing (LUM-157)** — a document **owner** can share a personal document with the household from the detail view (**`POST /api/v1/documents/{id}/publish`**, body `{"scope":"shared"}`) and revoke it (**`DELETE /api/v1/documents/{id}/publish`**). Both **validate ownership synchronously** (`404` foreign / non-personal, `400` invalid scope) then **enqueue a background job** and return **`202 {document_id, job_id, share_status}`**. Sharing does more than flip metadata: the **`share_document`** batch handler **projects the document's Qdrant chunks into `scope='shared'` by reusing the stored vectors** (no re-embed) so other members can genuinely **search and document-chat** the content; unshare removes only those shared copies. New job stages **`projecting`** / **`partial`** flow through **`ingest_progress`** and the **`GET /api/v1/ingest/jobs/{job_id}`** poll (a chunk-level upsert failure yields an honest **`partial`**, never a false `shared`). Concurrency is serialised per-document via a Postgres advisory lock (**`services/share_lock.py`**) so a share and a re-ingest re-projection can't race; re-ingesting a still-shared source **re-projects** shared chunks (**`reproject_shared_on_reingest`**) so members never see stale content; purging the source removes the shared chunks (no orphans). `DocumentSummary`/`DocumentDetail` gain **`share_status`** (`personal` / `sharing` / `shared` / `unsharing` / `partial`), **`in_flight_share_job_id`**, and **`is_owner`**; the owner's own projection row is collapsed in `list_documents` (one row per shared doc). Web: an owner-gated **`ShareToggle`** (plain-language confirm before sharing; non-owners see a read-only indicator), a **"Shared"** badge (precedence system > shared > personal), and a **"Shared with household"** filter on **`/documents`**. Owner-only enforcement is server-side (a forged non-owner call still `404`s); `is_owner` gates only the UI affordance. Requires migration **046** (partial `file_index_user_path_uniq` `WHERE published_from IS NULL`, so the shared projection row doesn't collide with the source). The lower-level raw route **`POST /api/v1/files/{id}/publish`** projects small documents inline but routes any document with more than **`LUMOGIS_SHARE_INLINE_MAX_CHUNKS`** chunks (default **50**) to the same background `share_document` job, so a large share never blocks the request thread. Re-sharing after a re-ingest **unprojects then re-projects** so the shared set exactly mirrors the current source chunks (no stale/orphaned shared points), chunk projection is gated to `scope='shared'` only, and each projected chunk requires an integer `chunk_index` (chunks missing one are skipped with a warning rather than colliding on a shared point id). — [ADR 155](decisions/155-lum-157-document-content-projection.md), scope model [ADR 015](decisions/015-personal-shared-system-memory-scopes.md).
- **Graph-aware document entity sharing (LUM-586)** — when a household document is shared (LUM-157), the **`share_document`** background job also cascades the document's **extracted entities** into `scope='shared'` (Postgres `entities` + Qdrant `entities` points, reusing LUM-581's `project_entity` with `share_origin='document'`), then fires a **`DOCUMENT_SHARED`** webhook so the KG service (`GRAPH_MODE=service`) MERGEs shared `:Entity` nodes and sweeps incident `RELATES_TO` edges between shared endpoints. The cascade is a **no-op when graph mode is `disabled`**. In **`GRAPH_MODE=inprocess`**, Core still creates the shared Postgres+Qdrant rows and fires the hook event, but **does not** project shared entities into FalkorDB — the in-process graph plugin deliberately excludes `on_document_shared`; operators who want graph-aware household recall after document share **must use `GRAPH_MODE=service`** (out-of-process `lumogis-graph` webhook + shared reconcile arm). Under **`GRAPH_MODE=service`**, the orchestrator has no in-process FalkorDB connection but dispatches the webhook to the KG service. **Refcounted retraction** on owner unshare (`unproject_file`), admin unshare (via `unproject_file` in the sharing registry), and hard purge (`document_purge`) removes doc-only shared entities when no other still-shared document justifies them; entities independently shared via LUM-581 (`share_origin='user'`) survive document unshare, and dual-provenance rows (`'multiple'`) downgrade to `'user'` rather than delete. A daily **shared-scope reconcile** arm (`reconcile_shared_entities`) backstops dropped webhooks. Requires migration **050** (`entities.share_origin`). Grouped-under-source-document UI in "My shared items" remains a deferred LUM-583 follow-up. — [ADR 158](decisions/158-graph-aware-entity-sharing-lum586.md), builds on [ADR 155](decisions/155-lum-157-document-content-projection.md).
- **Household entity sharing (LUM-581)** — an entity **owner** can share one of their own personal entities with the household, and unshare it, from the memory/entity surface in Lumogis Web. It reuses the **existing** publish routes (**`POST /api/v1/entities/{id}/publish`** body `{"scope":"shared"}`, **`DELETE …/publish`**) and the `project_entity`/`unproject_entity` Qdrant mirror — **no new route, no background job**. Unlike documents, an entity is a single vector point, so publish/unpublish is **synchronous** (`200/204`; no `sharing`/`partial` job stages). The entity read models (`EntityCard` from **`GET /api/v1/kg/entities/{id}`** and **`GET /api/v1/kg/search`**) gain **`share_status`** (`personal` / `shared` only) and **`is_owner`**, mirroring the LUM-157 cross-resource contract (`is_shared` is a derived convenience). Derivation is server-side and parameterised: an owner's personal source reads `shared` when a `published_from` projection exists for it, another member's projection row reads `shared` with `is_owner=false`, and the owner's own projection row is **collapsed** out of search results so they see one entity, not a duplicate. Web: an owner-gated **`EntityShareToggle`** (plain-language confirm before sharing; non-owners see a read-only "Shared with your household" indicator), a **"Shared"** badge, and a **"Shared with household"** filter on the search surface. Owner-only enforcement is server-side (a forged non-owner call still `404`s via the `WHERE user_id=%s AND scope='personal'` fetch guard); **staged** entities are refused (`409 entity_is_staged`) so an unreviewed extraction is never leaked. No schema or collection change. — reuses [ADR 155](decisions/155-lum-157-document-content-projection.md), scope model [ADR 015](decisions/015-personal-shared-system-memory-scopes.md).
- **Bootstrap admin** — first user creation when the table is empty and bootstrap env is set (see `.env.example` / ops docs).
- **Authentication** — `/api/v1/auth/*`: login, refresh (httpOnly cookie), logout. Access JWT carries **`sid`** (session id) and **`tv`** (token-version snapshot); middleware rejects stale **`tv`** vs Postgres. Staged enforcement: **`LUMOGIS_REQUIRE_TV_CLAIM`** (when **true**, missing **`tv`** → 401). Optional per-session revocation — **`LUMOGIS_REQUIRE_SID_REVOCATION_CHECK`** (default **false**) checks **`sid`** vs **`auth_sessions.revoked_at`** on each authenticated request when **`AUTH_ENABLED=true`** (no effect in single-user dev mode). **`LUMOGIS_SID_REVOCATION_CACHE_TTL_SECONDS`** (default **30**) bounds an in-process LRU for verdicts; **`0`** disables that cache so every verification hits Postgres (**operator diagnostics only**). Checks run **after** **`tv`** succeeds. **`GET/DELETE /api/v1/me/sessions`**, **`POST /api/v1/me/logout-all`**; admin session inventory under **`/api/v1/admin/users/{user_id}/sessions`**. Refresh cookie path **`/api/v1/auth`**. **`LUMOGIS_TOKEN_VERSION_CACHE_TTL_SECONDS`** bounds in-process **`tv`** cache (single-worker / **`--workers 1`** supported; multi-worker coherence → **LUM-30**).
- **Why not cloud multi-tenant:** Product target is **household LAN**; hosted multi-tenant is explicitly deferred ([ADR 012](decisions/012-family-lan-multi-user.md), remediation plan §1, portfolio).

**Password management (implemented):**

| Path | Purpose |
| --- | --- |
| First-run onboarding | `GET /api/v1/me/onboarding` (read `completed_at`), `PATCH /api/v1/me/onboarding` with `{ "completed": true }` (same-origin on `PATCH`) — Lumogis Web skippable modal; **`AUTH_ENABLED=false`** returns a synthetic completed timestamp so dev mode stays branch-free |
| First wow moment (chat) | `GET /api/v1/me/wow-state` (readiness + top entities + dismissal), `PATCH /api/v1/me/wow-state` with `{ "dismissed": true }` (same-origin on `PATCH`) — guided/discovery cards on **`/chat` only** after onboarding complete and ≥1 non-staged visible entity; **`AUTH_ENABLED=false`** synthetic ready state — [ADR 075](decisions/075-lum-216-first-wow-moment.md) |
| Self-service | `POST /api/v1/me/password` — UI on **Settings → Profile** |
| Admin reset | `POST /api/v1/admin/users/{user_id}/password` — **Admin → Users** |
| Household invite (copy-link) | Admin **`POST /api/v1/admin/users/invites`** mints a single-use `linv_` token (48h default, `LUMOGIS_INVITE_TTL_HOURS` override); public **`GET /api/v1/invites/{token}`** peek + **`POST …/redeem`** create the account and issue access JWT + refresh cookie; Lumogis Web **`/invite?token=…`** (public, inside `AuthProvider`); invite-redeemed members may see an extra household welcome onboarding step — [ADR 154](decisions/154-lum-186-household-invite-flow.md), LUM-186 |
| CLI recovery | From the **`orchestrator/`** directory: `python -m scripts.reset_password` (see `orchestrator/scripts/reset_password.py` docstring)—[ADR 029](decisions/029-self-hosted-account-password-management.md) |

**Household invites (LUM-186):** Migration **`044-user-invites.sql`** adds **`user_invites`** (hashed token, role, `allows_shared` metadata, expiry, single-use consume). Admin **Admin → Users → Invite member** generates a copy-link (`LUMOGIS_PUBLIC_ORIGIN` + `/invite?token=…` when origin is set; relative path in dev). Redemption is rate-limited and auth-bypass public (like login). v1 does **not** enforce per-user shared-scope visibility from `allows_shared` — onboarding copy only; shared/system rows remain visible per migration **013** until a follow-up capability lands.

**Deferred:** email / forgot-password / magic links (`lumogis_forgot_password_email_reset` class of work)—requires SMTP and abuse handling. SMTP invite delivery is likewise deferred (copy-link only v1).

---

## 7. Credentials and permissions

**Lay view:** Connectors hold the keys Lumogis needs (ntfy, calendars, LLM APIs). You paste them once; the UI does not show them again. Admins can help manage household-level configuration; some secrets are **per user**, some **shared** or **system** tier.

**Technical view:**

- **Per-user / household / system** credential scopes—[ADR 027](decisions/027-credential_scopes_shared_system.md).
- **Storage:** `user_connector_credentials` with **encrypted** payloads; **MultiFernet** allows key rotation—`orchestrator/services/_credential_internals.py`.
- **Connector permissions** (Ask/Do/blocked) — per-user rows; `get_connector_mode` drives catalog **labels** and execution checks—[ADR 024](decisions/024-per-user-connector-permissions.md).
- **`GET /api/v1/me/tools`** exposes **permission_mode** (`ask` / `do` / `blocked` / `unknown`) as **read model** only; granting/changing permissions is elsewhere.
- **Capability permission scopes (LUM-612, LUM-507 pillar a):** a capability's declared **`permissions_required`** (LUM-41 manifest) is **enforced least-privilege** — at the invocation chokepoint (`services/execution.py::execute_capability_http`) Core denies (fail-closed) any capability call whose required scopes are not in the user's **granted** set, naming the missing scopes so the caller can request a grant. A capability's permission identity is the connector **`capability.{manifest.id}`**; grants live in the ADR-024-reserved **`scopes TEXT[]`** column on `connector_permissions` (migration **052**), cached as one `(mode, scopes)` value so every existing invalidation path (revoke, `DELETE`, account-disable) drops scopes too. Grant/revoke via **`PUT /api/v1/me/permissions/{connector}`** (body `{"mode"?, "scopes"?}`; `scopes` present replaces the granted set, absent leaves it unchanged; scope strings are `area:verb`, 422 on malformed); the consent surface **`GET /api/v1/me/permissions/capabilities`** lists each registered capability's **required** vs **granted** vs **missing** scopes. Manifests whose `id` is not grant-shaped, or that declare a malformed required scope, are **refused at registration** (never silently locked out). The KG `query_graph` bridge is a trusted first-party tool and is **not** scope-gated.
- **Capability sandbox + egress (LUM-613, LUM-507 pillar b):** an out-of-process capability runs in its **own container** and makes its **own** outbound calls, so Core's in-process hooks **cannot** stop it from exfiltrating — the hard container-network guarantee is **LUM-618** (which gates the public community marketplace launch). This chunk ships the enforceable half: a **fail-closed community-dispatch gate** — a discovered capability is **community (untrusted)** unless its manifest `id` **and** discovery origin match the operator-maintained, origin-pinned allowlist `orchestrator/first_party_capabilities.txt`; a community capability is **refused dispatch** (structured `tool-unavailable`, host not contacted, and hidden from the LLM tool catalog) until an operator sets **`LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES=true`**. The trust boundary is **id-based** (deployment-independent; not the author-declared `license_mode`, not the compose service name) and **decoupled** from the LUM-43 credentials allowlist. Capabilities declare their egress hosts in the additive manifest field **`external_endpoints`** (bare hosts / IPv4, validated + bounded; read into the OOP tool audit and `/me/tools` visibility — **visibility, not consent**); the registry **refuses** a refresh whose origin conflicts with a registered `id` (shadow-takeover guard, loud `capability_identity_conflict` audit) and **evicts** an already-registered service that later returns an invalid manifest (never on a transient network blip). Untrusted plugins are **out-of-process only** — the in-process loader refuses any non-first-party module (ADR-170 §0). The two `tethered.scope()` defense-in-depth wraps are deferred to **LUM-619** pending a concurrency/nesting verification; LUM-613 adds no `tethered` import. This layer never claims to *prevent* OOP egress — it declares, gates dispatch, and audits.
- **Container-network egress containment (LUM-618 / LUM-621, LUM-507 pillar b-hard):** the **hard** guarantee LUM-613 deferred, and the gate for the public community marketplace. On Docker, a community capability runs on an **`internal: true`** network (`enable_ipv6: false`) — Docker gives it **no route to the internet** — and its only egress path is a dedicated, profile-gated **Squid** proxy that allowlists **only** the capability's declared `external_endpoints`: HTTPS by TLS **SNI** via `peek`+`splice` (**no decryption / no MITM / no CA injection** — end-to-end TLS is preserved), HTTP by `dstdomain`, deny-all default. Default compose image is **digest-pinned** (`EGRESS_PROXY_IMAGE=jacobalberty/squid@sha256:…`, linux/amd64). The dispatch gate dispatches a community capability iff its id is in a **containment marker** (`contained_capabilities.txt`), which **supersedes** the deprecated `LUMOGIS_ALLOW_UNCONTAINED_COMMUNITY_CAPABILITIES` flag (retained, back-compat, one-time deprecation warning). The marker is **mtime-reloaded** on the next OOP dispatch (no Core restart; keep-last-good if the file disappears). The image-default marker is **empty** (fail-closed); the egress overlay supplies it via `LUMOGIS_CONTAINED_CAPABILITIES_FILE`, so "contained" cannot be asserted without the network wiring that backs it. Install-time `scripts/gen_capability_egress_acl.py` turns a manifest's `external_endpoints` into the proxy allow file; **`make check-egress-acl-divergence`** (`--check`) fails closed when the committed allow file drifts from declared endpoints (CI beside compose-policy egress). When the egress overlay is active, Squid deny lines in the shared access log produce a structured Core **`egress.denied`** WARNING (hostname-only, deduped) — spike outcome **A** confirmed HTTPS `TCP_DENIED` is logged under the pinned `lumogis_egress` logformat. **Compose-policy Pass C** (`scripts/check_compose_policy.py --community-service`) verifies in CI that a community service is on the isolated network and on **no** internet-facing network. Hard enforcement is **Docker-only**; native deployments keep community capabilities refused (fail-closed on the LUM-613 gate). Known residuals (documented): SNI/Host divergence on shared-infrastructure hostnames, IPv4-literal `dst` ACL, ECH, and signing (**LUM-614**).

**Examples:** **ntfy** — notification channel connector ([ADR 022](decisions/022-ntfy-runtime-per-user-shipped.md)). **LLM provider keys** — per-user ([ADR 026](decisions/026-llm-provider-keys-per-user.md)). **CalDAV** — [ADR 021](decisions/021-caldav-connector-credentials.md). **MCP tokens** — [ADR 017](decisions/017-mcp-token-user-map.md).

**Cloud LLM privacy mode ([ADR 147](decisions/147-lum-194-cloud-llm-privacy-mode.md), LUM-194):** Household **routing policy** (not network isolation) that blocks **remote** models when local-only is effective. Instance keys in `app_settings`: `privacy_mode` (`local_only` | `allow_cloud`, default `local_only` when absent on fresh install) and optional `privacy_mode_locked`. Per-user further restriction (`local_only` only — cannot expand beyond instance) lives in `privacy_user_settings` when `AUTH_ENABLED=true`. Enforcement is at `config.get_llm_provider` / `is_model_enabled` using a fail-closed allow-list (`is_local_model()`). Admin configures instance policy at **Admin → Privacy mode** (`/admin/privacy-mode`); members may further restrict at **Me → Privacy mode** (`/me/privacy-mode`) when the instance allows cloud and is not locked. Chat requests for a blocked remote model fall back to a local model with `lumogis.privacy` metadata (non-streaming JSON and first SSE chunk); hard blocks emit `privacy_mode_block` audit rows (JSON includes `decline_type: external_call_denied` for LUM-137). Migration **043** seeds `allow_cloud` on upgrade when the household already demonstrated cloud usage.

**Egress guard ([ADR 153](decisions/153-lum-553-tethered-egress-allowlist.md), LUM-553):** Optional **defense-in-depth** behind ADR 147 — **not** a substitute for routing policy. When **`LUMOGIS_FF_EGRESS_GUARD=true`** (default **off**), Core wraps LLM adapter calls with an in-process socket allowlist (`tethered` PEP 578 `scope()`). Allowed hosts are derived from local backends (Postgres, Qdrant, FalkorDB, ntfy, Ollama), `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST`, and cloud API hosts only when effective privacy policy permits cloud. Blocks surface as HTTP **503** `egress_blocked` on non-stream chat, or in-band SSE errors on streaming (HTTP 200). Operator copy must state this is **bypassable** and does **not** guarantee network isolation. Process-wide ceiling (`LUMOGIS_FF_EGRESS_GUARD_CEILING`) is reserved for a future chunk — not implemented in v1.

**Security note:** Do not log secrets; diagnostics and façades avoid env dumps and raw ciphertext. Copy-once tokens where the product uses that pattern. Passwords are never shown again after set.

---

## 8. Memory, search, entities, and context

**Lay view:** Lumogis keeps **structured records** in a database and a **semantic index** for “fuzzy” finding. When you chat, it pulls snippets that matter, trims them to a budget, and **only that bundle** may go to an external model if you use one.

**Technical view:**

- **Postgres** — authoritative metadata, sessions, entities tables, audit, signals, etc.
- **Qdrant** — vector (and optional sparse/hybrid) search; **user_id** / scope visibility filters on queries for multi-user isolation (`visible_qdrant_filter`). Live two-user semantic-search isolation (real `Filter(should=…)` ANN) is regression-tested under compose in `orchestrator/tests/integration/test_two_user_qdrant_isolation_live.py` (**LUM-307**, [ADR 156](decisions/156-lum-307-two-user-qdrant-isolation-test.md)); unit translation remains in `test_qdrant_store_filter_build.py` (**ADR 057**).
- **Memory scopes** — personal, shared, system—[ADR 015](decisions/015-personal-shared-system-memory-scopes.md).
- **Household biography conflict policy (LUM-514, [ADR 147](decisions/147-lum-514-household-biography-conflict-resolution.md))** — when multiple household members publish **shared-scope** biography pins that disagree on the same fact group, Core **detects** a conflict (never silent last-writer-wins among divergent values). Default synthesis behaviour is **represent-both with attribution** (`user_id` labels). Household **admins** resolve via **`POST /api/v1/biography/conflicts/{id}/resolve`** (`confirm_one`, `keep_both`, `dismiss`); any member may **list** open conflicts (`GET /api/v1/biography/conflicts`). Audit rows live in Postgres table **`biography_conflict_resolutions`** (migration **043**). **`category=identity`** and single-author households are exempt (strict no-op). Production detection is invoked by the LUM-516 synthesis job (`detect_and_persist_open_conflicts`); **`biography_pins`** lands in LUM-515.
- **Entity extraction** — stored and linked per ingestion/session flows—see `services/entities.py`, entity ADRs ([014](decisions/014-entity-relations-evidence-dedup.md), etc.).
- **Entity summary write isolation (LUM-358, [ADR 154](decisions/154-lum-358-household-concurrent-write-isolation.md))** — Postgres `entities` rows carry monotonic **`version`**, live **`summary`**, and consolidation **`staged_summary`** (migration **044**). Future summary writers (LUM-108 write-back, LUM-109 consolidation) must use **`services.entity_write_guard`** for read-version / guarded-commit (`UPDATE … WHERE version=$read_version`); **`scope='system'`** summaries are read-only for household members in v1. Consolidation runs acquire a **non-blocking** per-`(scope_owner, entity_type)` **`pg_try_advisory_lock`** on a **dedicated checkout connection** via **`services.consolidation_lock`** (salt **8421358**; hold only for fast claim/stage — never across LLM inference). Multi-user field-tier merge rules live in **`services.entity_conflict_policy`** (LUM-514 vocabulary for shared-scope divergence). Extraction paths do not yet mutate `summary`.
- **Context building** — retrieval + `context_budget` truncation **on plaintext fragments** before LLM calls (`ARCHITECTURE.md`, `orchestrator/services/context_budget.py`). Graph **`Event.CONTEXT_BUILDING`** subscribers receive **`query`**, **`context_fragments`** (mutated in place), and **`user_id`**; entity pick uses **`select_context_entities`** (hybrid explicit + optional Qdrant semantic pass, ranked and capped) — **[ADR 051](decisions/051-context-building-hybrid-entity-selection.md)** (**LUM-210**). Operator env (Core + KG mirror): **`LUMOGIS_CONTEXT_ENTITY_BUDGET`**, **`LUMOGIS_CONTEXT_BUILDER_SEMANTIC`**, **`LUMOGIS_CONTEXT_BUILDER_SEMANTIC_TOPK`**, **`LUMOGIS_CONTEXT_BUILDER_SEMANTIC_THRESHOLD`**, **`LUMOGIS_CONTEXT_ENTITY_RANK_*`**, **`LUMOGIS_CONTEXT_ENTITY_EXPLICIT_BONUS`**. Allocator exposes a dedicated **`entities`** slice for `[Graph]` plaintext in addition to **`session_context`** / **`plugin_context`**. **Memory-as-hint ([ADR 066](decisions/066-lum-124-memory-as-hint.md), LUM-124):** when retrieval produced at least one plaintext fragment, Core appends a short operator English hedge on the assistant **ack** path (session, document, and graph excerpts are **hints**, not ground truth), gated by **`LUMOGIS_MEMORY_HINT_ENABLED`** (default **on**); the injection-sanitiser path **prepends** the hedge **before** the nonce-attested scaffolding. **`[Graph]`** entity lines (built in **`services/lumogis-graph/graph/query.py`**) may include a frozen metadata suffix **`(hint: type=…; confidence=…; last_seen=…)`** derived from Postgres **`entities.memory_type`**, **`mention_count`**, **`updated_at`** / **`last_verified_at`**, and per-**`entity_type`** half-life envs (**`LUMOGIS_ENTITY_HALFLIFE_*_DAYS`**, including **`FILE`**). Optional dev flag **`LUMOGIS_MEMORY_CORRECTION_PLACEHOLDER`** (default **off**) allows a Falkor **`memory_type='correction'`** projection from **`FEEDBACK_RECEIVED`** until **LUM-114** ships durable feedback ingest.
- **Document auto-RAG ([ADR 059](decisions/059-lum-308-document-auto-rag-chat.md), LUM-308)** — optional **`LUMOGIS_AUTO_RAG_ENABLED`** (**default `false`**) lets each **`POST /v1/chat/completions`** retrieve gated chunks from the Qdrant **`documents`** collection (same **`user_id` / scope visibility** as **`GET /search`**), score with the configured BGE reranker when present, and inject plaintext **after session summaries and before graph lines** into the same ADR 039 bundle as today. Injected Qdrant **`point_id`** values are tracked for the request so **`search_files`** can omit duplicates without a second round-trip. Tune **`LUMOGIS_AUTO_RAG_*`** via **`.env.example`**; **`docs/capabilities.md`** notes latency and path/token privacy when enabled.
- **Document chat mode (LUM-175)** — **`POST /api/v1/chat/completions`** with optional **`document_id`** (`file_index.id`) pins a turn to one library document: scoped retrieval bypasses global auto-RAG off, skips session/graph context, forces **`use_tools=false`**, and returns **`lumogis.context_citations`** (chunk indices). Web route **`/documents/:documentId/chat`** shows a **Context used** strip. Errors: **`document_not_found`** (404), **`document_context_unavailable`** (422), **`invalid_document_id`** (422), **`auto_rag_failed`** (503). Env: **`LUMOGIS_DOCUMENT_CHAT_TOP_K_PRE`** / **`LUMOGIS_DOCUMENT_CHAT_TOP_K_POST`** — [ADR 101-lum-175](decisions/101-lum-175-document-chat-mode.md).
- **Document injection hardening ([ADR 039](decisions/039-document-injection-sanitisation.md), LUM-127)** — by default (**`INJECTION_SANITISER_ENABLED=true`**, **`INJECTION_ACTION=wrap`**) Core runs a YAML regex/heuristic table at **ingest** and wraps retrieved corpus in **`<retrieved_chunk …>`** scaffolding before it is merged into chat context or **`search_files`** tool JSON; session + graph snippets are folded into a **`<lumogis_injected_context request_nonce='…'>`** envelope with nonce-bearing assistant scaffolding. **`Event.CONTEXT_BUILDING`** subscribers still observe **plaintext** fragments; tagging runs **after** the synchronous hook (+ service-mode KG extend). **`TOOL_CHAIN_CAP`** (default **`10`**) pessimistically caps parallel tool dispatches per chat completion (**`lumogis_blocked`** JSON stub when tripped). Pattern file **`INJECTION_PATTERN_FILE`** must resolve **under** the packaged **`orchestrator/data/`** subtree (same rule as bundled default **`injection_patterns.yaml`**). Operators who need to pause the feature temporarily can set **`INJECTION_SANITISER_ENABLED=false`** (patterns are not loaded; compaction trust prefix skipped in **`memory.py`**).
- **If a cloud LLM is used:** **Composed prompts and retrieved excerpts** (after context budgeting) are sent to the provider—they **may contain private text** from your index. Lumogis does **not** ship the **entire** local corpus or disk image as a bulk upload; what leaves is **what Core assembled for that request**. **Connectors** are separate: they contact **their** external APIs when invoked, by design.

---

## 9. Actions, tools, and audit

**Lay view:** Some operations are safe to run immediately (**Do**); others must wait for your **approval** (**Ask**). Everything important is logged.

**Technical view:**

- **Ask vs Do** — [ADR 006](decisions/006-ask-do-safety-model.md); executor enforces mode; destructive actions stay Ask.
- **Action registry** + **handlers**; **audit_log** append-only—[ADR 019](decisions/019-structured-audit-logging.md). **`GET /api/v1/audit`** lists rows for the authenticated user with optional `connector`, `action_type`, `event_type` (namespaced taxonomy or category prefix), `after`/`before` (422 when inverted), `limit`/`offset`, and admin-only `as_user`. Responses include `total` and enriched per-row `event_type`, `scope`, `source`, and `description` (derived server-side from existing summaries — [ADR 156](decisions/156-lum-197-audit-log-ui.md), LUM-197).
- **ToolCatalog** — `build_tool_catalog` / `build_tool_catalog_for_user` — read-only inventory with transports (`llm_loop`, `mcp_surface`, `catalog_only`).
- **ToolExecutor** — `execute_inprocess` / `execute_capability_http` with `PermissionCheck` and audit envelope; **OOP** capability calls fan in to `audit_log` (`tool.execute.capability` style rows)—see `tool-vocabulary.md`.
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** — default **`true`** when unset (`config.get_tool_catalog_enabled()`); set **`false`** (or **`0`** / **`no`**) to opt out. When **false**, the LLM loop does **not** merge OOP capability tools into the live tool list. When **true**, `prepare_llm_tools_for_request` may append healthy, bearer-authenticated capability tools; **`finish_llm_tools_request`** must run after each request (`loop.py` try/finally)—see `services/unified_tools.py`.
- **Continue Site session state ([ADR 099](decisions/099-lum-128-continue-site-pattern.md), LUM-128):** `ask()` / `ask_stream()` share `_run_session_loop()` with frozen **`SessionState`** (`orchestrator/models/session_state.py`) rebuilt via `dataclasses.replace()` at model-call, tool-dispatch, turn-advance, and terminate sites. **`ToolChainBudget`** lives on `SessionState`; sync and stream paths preserve distinct tool-dispatch predicates (`stop_reason` vs non-empty stream tool calls). **`on_loop_event(event, state)`** is in-process only (LUM-139 may subscribe later). LUM-122 compaction will hook at PRE_REQUEST (`_inject_context` + comment seam in `routes/chat.py`).
- **Fail-closed:** Missing bearer, unhealthy capability, or denied permission should not silently execute OOP tools.
- **Action proposal queue ([ADR 040](decisions/040-action-proposals-atomic-claim.md), LUM-123)** — Approved proposals live in Postgres table **`action_proposals`** (migration **`022`**). **`claim_next`** / **`claim_by_id`** promote **`approved` → `executing`** atomically (SKIP LOCKED for drain). **`POST /api/v1/approvals/proposals/{proposal_id}/execute`** claims then runs **`actions.executor.execute`**; concurrent losers get **409** (`proposal_claim_conflict`). Stale **`executing`** rows are swept to **`dead`** (not re-approved). Env: **`ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS`** (default **300**), **`ACTION_PROPOSALS_CLAIM_SWEEPER_SECONDS`** (default **60**), **`ACTION_PROPOSALS_MAX_ATTEMPTS`** (default **3**), **`ACTION_PROPOSALS_QUEUE_ENABLED`** (default **true**, APScheduler sweeper only). With **`AUTH_ENABLED=false`**, admin **`as_user`** is broadly available (same footgun as **`GET /pending`**); proposal execute is side-effecting. Standard per-user **export** omits **`action_proposals`** (operational queue — see **`user_export._OMITTED_USER_TABLES`**).

**Flow (simplified):** User asks → Core builds context (retrieval + budget) → LLM may emit tool call → permission / Ask-Do check → `run_tool` / executor → handler or HTTP proxy → **audit_log** (+ structured logs).

---

## 10. Signals and routines

**Signals** — External or scheduled inputs (RSS, page change, calendar, **paperless-ngx document archive (LUM-281)**, system). Monitors poll; processor scores and persists; plugins can react via hooks.

#### Filesystem inbox auto-ingest (LUM-330, operator)

- **Purpose:** Drop supported files into **`ai-workspace/inbox/`** (bind-mounted as **`/workspace/inbox`** in the orchestrator container) and Core ingests them without **`POST /ingest`**. Failures that are not transient move to **`ai-workspace/quarantine/`** with a sibling **`.error.json`** sidecar (not counted in inbox depth alerts).
- **Owner:** Set **`INBOX_OWNER_USER_ID`** to the Lumogis **`user_id`** that owns dropped files (required for watcher or poll; no silent **`default`** fallback). When unset, neither the **`watchdog`** observer nor the poll job starts.
- **Mode (`LUMOGIS_INBOX_MODE`):** **`event`** (default) — in-process **`watchdog`** observer with write-stability before ingest; **`poll`** — APScheduler job **`inbox_poll`** scans the inbox on an interval (use on SMB/NFS or macOS Docker Desktop bind mounts where inotify is unreliable); **`off`** — no observer and no poll job. Invalid mode values log **ERROR** at startup and coerce to **`off`** (fail-closed).
- **Paths:** **`LUMOGIS_INBOX_PATH`** defaults to **`/workspace/inbox`**; relative values resolve under **`WORKSPACE_PATH`** (default **`/workspace`**). Quarantine is always **`{WORKSPACE}/quarantine/`** (sibling of inbox, outside the watched tree).
- **Tuning:** **`LUMOGIS_INBOX_STABILITY_DELAY_MS`** (default **1500**) — max wait for stable **(size, mtime)** before ingest; **`LUMOGIS_INBOX_POLL_INTERVAL_S`** (default **10**, floor **5** in config) — poll interval; **`LUMOGIS_INBOX_MAX_FILE_MB`** (default **200**) — skip oversized files before calling extractors. Poll mode calls **`inbox_poll_should_ingest`** (mtime vs **`file_index.updated_at`**) before **`ingest_file`** so unchanged files are not re-hashed every tick; event mode always ingests after stability.
- **Partial writes:** Basenames starting with **`.`** and suffixes **`.tmp`**, **`.part`**, **`.crdownload`** are ignored. **`on_moved`** handles intra-inbox atomic renames (e.g. editor **`file.tmp` → `file.pdf`**). After **three** consecutive stability timeouts in poll mode, the file is quarantined with reason **`stability_timeout`**.
- **Observability:** **`GET /healthz`** merges **`inbox_mode`**, **`inbox_watcher`** (`ok` | `degraded` | `disabled`), and (poll only) **`inbox_poll_last_scan`** — **no absolute paths** on the unauthenticated probe. **`GET /api/v1/admin/diagnostics`** (auth-gated) adds **`inbox_path`** and the same fields. **`system_monitor`** inbox depth uses the configured inbox path (top-level file count only; recursive watcher vs depth alert is a known limitation).
- **ADR:** [070-lum-330-folder-watch-inbox](decisions/070-lum-330-folder-watch-inbox.md).

#### paperless-ngx → Lumogis ingest (v0.1, operator / admin API)

- **Purpose:** OCR’d text from a self-hosted **paperless-ngx** instance is polled over its REST API (`/api/documents/`) with **token** auth, deduped in Postgres (`external_documents`), and embedded into the normal **`documents`** Qdrant collection. No new default Compose service — wire paperless yourself on the Docker network.
- **Credentials:** `PUT /api/v1/me/connector-credentials/paperless` with JSON payload `{"base_url":"http://paperless:8000","token":"<API token>"}` (registered connector id **`paperless`**). When **`AUTH_ENABLED=false`**, optional env fallbacks **`PAPERLESS_BASE_URL`** + **`PAPERLESS_TOKEN`** apply only to **`user_id="default"`** (single-user dev), mirroring CalDAV’s env trio pattern.
- **Source row:** Admin `POST /api/v1/sources` with **`"source_type": "paperless"`** and **`url`** set to the same API base (trailing slash trimmed). **`confirm:false`** returns `{"source_type":"paperless","url":<normalized>,"preview_items":[]}` without RSS detection. **`confirm:true`** inserts into **`sources`** with **`extraction_method":"paperless_http"`** and schedules polling (floor **60s**).
- **Incremental cursor:** First poll omits `added__gt` (full history, ordered by `added`) — large libraries need many ticks under per-tick caps. Later polls use **`added__gt`** watermark stored in **`sources.poll_cursor`** (ISO timestamps from paperless).
- **Outbound URL / SSRF:** `base_url` is validated via **`validate_outbound_connector_base_url`** on credential read, credential save path (via payload validation), and the **`POST /sources`** paperless branch. **`169.254.0.0/16`** is always blocked. Private / loopback / RFC1918 / ULA ranges are allowed when **`LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS`** is **`true`** (default for LAN Docker). Set it to **`false`** and use **`LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST`** (comma- or space-separated hostnames, e.g. `paperless`) to allow specific hostnames that resolve to private IPs. If DNS does not resolve at validation time, only scheme/host shape checks run — residual **DNS rebinding** risk is mitigated by pinning paperless on a trusted network.
- **Paperless version:** **≥ 2.16.1** recommended (timezone fixes upstream).
- **Webhook consumers:** handle optional **`ingestion_source_kind`** on **`DOCUMENT_INGESTED`**; branch on **`"external"`** when **`file_path`** uses the **`paperless://`** URI scheme.

**Routines** — Scheduled automation elevating trusted **Ask** actions toward **Do** after enough clean approvals (`ROUTINE_ELEVATION_THRESHOLD`).

**Notifications** — Core signals, digest, and routine-elevation paths route through a unified in-process **dispatcher** ([ADR 077](decisions/077-lum-189-notification-architecture.md), **LUM-93** chunk 1 shipped) with **ntfy**, **Web Push**, and **in-app SSE** channel adapters. Per-user **routing preferences** (notification type × channel) live in Postgres (`notification_preferences`, `notification_user_settings`) and are editable in Lumogis Web **`/me/notifications`** ([ADR 098](decisions/098-lum-93-notification-settings-ui.md)). The read-only **`GET /api/v1/me/notifications`** channel-status façade remains separate from prefs. **Quiet-hours** columns exist but full policy UI is **LUM-144**; dispatcher applies server-side quiet-hours gate when start/end are set (UTC fallback when timezone unset).

**Web Push prefs bootstrap (operator recovery):** On startup, core runs a one-time idempotent seeder keyed by `webpush_prefs_seeded_v1` in `app_settings`. If it fails, boot continues (fail-open per ADR 077/098); orchestrator logs `webpush_prefs_seeder: failed` at error level and tier defaults apply until a successful run. Fix the underlying DB/connectivity issue and **restart core** — the seeder retries automatically because the success flag was not written.

**Implemented vs planned:** Dispatcher + prefs API + editable Web matrix are **shipped**. Browser **Web Push** subscription plumbing and minimal templates are **shipped**—see **§13**; **`ACTION_EXECUTED`** Web Push templates and inbox persistence remain **LUM-28** / **LUM-174**. Pushing notifications for **every** connector/action outcome category remains **incomplete**. The **ntfy server** remains optional; the **ntfy mobile app is not required** — Web Push and in-app SSE cover Lumogis Web surfaces.

---

## 11. Knowledge graph

**Lay view:** An optional “knowledge graph” can track relationships between entities beyond flat search. You do **not** need it for basic chat and RAG.

**Technical view:**

- **In-process vs service:** Graph routing is described in **[ADR 002](decisions/002-graph-store-falkordb.md)**; premium operators enable `GRAPH_MODE=inprocess` or `GRAPH_MODE=service` with matching modules. **Default `GRAPH_MODE` is `disabled`.**
- **FalkorDB** — backing store for KG in service mode; see `docker-compose.premium.yml` / FalkorDB overlays. Optional **`docker-compose.falkordb.yml`** publishes **`127.0.0.1:${FALKORDB_HOST_PORT:-6380}:6379`** so host-side tools (including **`make m1-compat-with-retry`**) can use **`FALKORDB_URL=redis://127.0.0.1:6380`** without container IP discovery; in-network services keep **`redis://falkordb:6379`** (**[ADR 053](decisions/053-lum-237-falkordb-host-port-loopback-publish.md)**).
- **`query_graph` tool** — When bridged over HTTP (capability invoke contract v1 — **[ADR 169](decisions/169-lum-41-capability-invoke-contract-v1.md)**), Core POSTs the wrapped request envelope to the tool's declared **`invoke.path`** (KG: `/tools/query_graph`, tool name `graph.query`) and parses the **`{ok, output|error}`** response envelope—[`tool-vocabulary.md`](architecture/tool-vocabulary.md) and **[ADR 002](decisions/002-graph-store-falkordb.md)**.
- **Temporal validity (LUM-104, flag-gated, default off):** with **`LUMOGIS_FF_TEMPORAL_KG`** on (requires `GRAPH_MODE=inprocess` in v1), KG edges carry a two-axis temporal model (`created_at`/`valid_at`/`invalid_at`/`expired_at` + `edge_uid`), graph reads filter out invalidated/expired edges (null-tolerant — legacy edges pass), and a background Core-side pipeline extracts real-world `valid_at` and detects contradictions with local models. Contradictions **archive** facts as no-longer-current (soft `invalid_at` + a `SUPERSEDES` history edge) — Lumogis never destroys memories; forgetting stays a deliberate user act (LUM-544). Auto-apply is **off** (shadow-mode proposals only) until the hand-graded eval meets the precision bar on the reference judge model; admin backfill via **`POST /graph/backfill/temporal`**. See **[ADR 151](decisions/151-lum-104-temporal-validity.md)** (and ADR 150 for the build-vs-wrap decision).
- **KG-specific vs generic:** KG proxy and manifest are the **reference capability**; generic discovery, health, `/tools/{name}`, and bearer env patterns apply to **any** capability.

---

## 12. Optional capability services

**Plain English:** A capability is an **extra program** Lumogis can discover—like a plug-in module, but running in its **own container**, with its **own data** if needed.

**Technical contract (invoke v1 — [ADR 169](decisions/169-lum-41-capability-invoke-contract-v1.md)):**

- `GET {base}/capabilities` — `CapabilityManifest` (`orchestrator/models/capability.py`, vendored to `services/lumogis-graph/models/capability.py`); includes `contract_version`, per-tool `invoke {method, path}`, `is_write`/`idempotent`/`timeout_ms`, and manifest `auth`.
- `GET {base}/health` — liveness for registry (`health_endpoint` on the manifest).
- **Invoke** — Core POSTs to each tool's declared **`invoke.path`** (default `/tools/{name}`) the v1 request envelope `{contract_version, tool, arguments, meta}`; the capability returns `{ok:true, output}` or `{ok:false, error:{code, message, retryable}}`. Core validates `output` against a non-trivial declared `output_schema` and maps transport failures into the same error vocabulary. Bearer tokens use `LUMOGIS_CAPABILITY_BEARER_<SANITIZED_ID>` (fail-closed when `auth.mode="bearer"` and unset); **`X-Lumogis-User`** / **`meta.user`** are **attribution only**, not authentication or data scoping — per-user scoping uses **`arguments.user_id`**.
- **No shared Core DB credentials** on capability containers—policy in remediation Phase 5.

**Mock capability:** `services/lumogis-mock-capability/` + `make mock-capability-test`; compose overlay `docker-compose.mock-capability.yml` — **not** in default `docker-compose.yml`.

**Status:** Phase 5 **scaffolding** is **sufficient for self-hosted** experiments; **Phase 6** marketplace / signed manifests / mTLS-by-default is **deferred**—see **§17**.

---

## 13. Lumogis Web

**Purpose:** First-party **household** UI: same-origin with Core via Caddy, consumes **`/api/v1/*`** for auth and control surfaces.

**Completed surfaces (representative):**

| Area | Routes / behaviour |
| --- | --- |
| Chat / search / approvals | Core product pages (Phase 1 baseline) |
| Capture | **`/capture`** (QuickCapture UX) and **`/api/v1/captures`** staging APIs (create/list/update/delete, attachments, transcription hooks, index submission) |
| **Me** | `/me/profile` (password change), `/me/connectors`, `/me/permissions`, `/me/llm-providers`, `/me/mcp-tokens`, `/me/notifications`, `/me/export`, `/me/tools-capabilities`, `/me/privacy-mode`; **`/audit`** member audit log (read-only; Settings nav via shared `MeSubshell` — LUM-197) |
| **Admin** | `/admin/users` (import/export, password reset), `/admin/connector-credentials`, `/admin/connector-permissions`, `/admin/mcp-tokens`, `/admin/audit`, `/admin/diagnostics`, **`/admin/system-status`** (live stack health — services, storage, **DR backup** and **Software updates** cards — **LUM-185**, **LUM-524**; Ollama list plus **pull/delete** via typed **`/api/v1/admin/ollama/*`** — **LUM-451**; SPA **async pull** with progress bar via `POST /api/v1/admin/ollama/pull/async` (202 + `job_id`) and poll `GET /api/v1/admin/ollama/pull/jobs/*`; legacy `/settings/ollama-*` remain for the HTML dashboard only; sync blocking pull has **no** v1 route; registry-alias and embedding badges; embedding pull may return **`qdrant_init_warning`** when Qdrant collection init fails after a successful pull — **LUM-452**; **LUM-178** slice 1–2c, **LUM-423**, [ADR 074](decisions/074-lum-178-stack-health-dashboard.md), [ADR 086](decisions/086-lum-449-async-ollama-pull.md), [ADR 088](decisions/088-lum-451-ollama-api-v1-promotion.md), [ADR 147](decisions/147-lum-524-admin-update-banner.md)) |

**Password management** — shipped per [ADR 029](decisions/029-self-hosted-account-password-management.md).  
**Admin import/export** — inventory + dry-run/real import via `/api/v1/admin/user-imports`; per-user export via `/api/v1/me/export` with `target_user_id`—see [`clients/lumogis-web/README.md`](../clients/lumogis-web/README.md). Refusal → HTTP semantics (including **413** policy cap vs reserved **507** for host storage exhaustion) are documented in [`docs/guides/per-user-export-format.md`](guides/per-user-export-format.md).

### Persona A / B / C — distribution matrix

Lumogis clients connect to **one Core per household**. **Persona A** and **Persona B** use the **same** client-only **Lumogis Search** installer (`lumogis-overlay-*` from public `search-v*` releases); only the **server URL** differs. Persona A install steps: [`../clients/lumogis-search/README.md#persona-a--docker-track-localhost`](../clients/lumogis-search/README.md#persona-a--docker-track-localhost). Server URL and HTTPS guidance: [`deployment/remote-access.md`](deployment/remote-access.md).

| Persona | Who | Core location | Lumogis Search | Typical server URL | Install path (summary) |
| --- | --- | --- | --- | --- | --- |
| **A** | Self-hoster operator | Docker Compose on the **same machine** as Search | Client-only Search installer | `http://localhost` or an operator-published origin on the host | `docker compose up -d` → install `lumogis-overlay-*` → point URL at Core |
| **B** | Household member | Remote household Core (operator's server) | Same client-only Search installer | Household URL (LAN or [remote access](deployment/remote-access.md)) | Download release from operator → set URL from operator |
| **C** | Non-technical operator | **Lumogis Server** native Core install (Docker-free, tray-first supervisor) | **Browser** → loopback Core (`/web/`, `/dashboard`); **Lumogis Search** overlay is a separate client (fast-follow **LUM-455** / **LUM-475**) | `http://127.0.0.1` (local Core) | Install **Lumogis Server** (`make server-build`); open Web via tray or browser |
| **C (admin on laptop)** | Operator away from the server host | Remote household Core (Server host URL) | **Client-only** Search installer (same artefact as B) | Household Core URL | Install `lumogis-overlay-*`, point at Server host — **Persona B settings layout** (`profile !== "bundled"`) |

**Lumogis Search overlay (LUM-329, LUM-397, LUM-398, LUM-430, LUM-457):** the AGPL **Tauri 2** app under **`clients/lumogis-search/`** provides a **global hotkey** frameless overlay for **`GET /api/v1/memory/search`** with **native open/reveal** under configured **library roots** (optional for search — required only to open files locally; not auto-synced with server **`ingest_paths`**). First-run in-app onboarding: household server URL → **`GET /healthz`** → sign-in when auth is on. When **`AUTH_ENABLED=true`**, the overlay uses **email/password login**, stores a single **`session:{host-hash}`** keychain entry (JSON session: access, refresh, expiry, role), and **reactively refreshes** on **`401`** via **`POST /api/v1/auth/refresh`** (manual **`Set-Cookie`** parse — reqwest has no cookie jar; refresh sends **Bearer + refresh cookie** per server CSRF bypass). **Admins** edit **`ingest_paths`** via **`GET/PUT /settings`**, see **`paperless_configured`**, and can **push-upload** files via **`POST /api/v1/ingest/upload`**. Non-admin household members may still use push upload. Build: **`make search-dev`** / **`make search-build`**. See **`clients/lumogis-search/README.md`**. After first-run onboarding completes, the overlay may show a **one-time** bottom-centred hint naming the configured global hotkey (e.g. *Press Ctrl+Shift+L anytime to open search*); it dismisses on window blur or after a few seconds and does not return unless webview storage is cleared (**LUM-456**). **OS system tray (LUM-457):** from first paint, a tray icon is installed via shared Rust **`system_tray`** — **left-click** toggles show/hide (same as the hotkey); **right-click** opens a menu with **Show Lumogis** and **Quit** (tray **Quit** exits the app). Tray complements the hotkey; on **Linux Wayland** tray icon visibility is best-effort (GNOME StatusNotifier / AppIndicator). **Linux Wayland:** global shortcuts are not a v0.1 target.

**Lumogis Server (LUM-469, ADR-094):** the **Persona C server** product (`com.lumogis.server`) is a separate Tauri profile from the legacy fused Hub — same supervisor stack (Postgres → Qdrant → Core) but **tray-first** steady state: after the setup wizard completes the main window hides, **no overlay mode**, and the operator uses the **system tray** to open **Core Web admin** (`/dashboard`) or family web (`/web/`) in the default browser. Build: **`make server-build`** / **`make server-dev`**; prove: **`make server-prove-server-profile`**. Optional **start at graphical login** on Linux via **`make server-install-systemd-user`** (`systemd --user`, `graphical-session.target`). Shares the Hub **`LUMOGIS_DEFER_LIBRARY_INDEX`** defer path — **[ADR 096](decisions/096-hub-library-index-cold-start-resync.md)** cold-start library resync when a prior index exists (**LUM-477**). Client v1 is **browser → loopback Core**; Search overlay client remains a fast-follow (**LUM-455** / **LUM-475**). See **`apps/lumogis-server/README.md`** (server profile section).

**Lumogis Hub (LUM-396, LUM-435, LUM-457, LUM-460, LUM-464, LUM-466, LUM-491) — legacy fused appliance:** the **retired** single-app Persona C product (`com.lumogis.hub`, `make hub-build-bundled` → `Lumogis_*.deb`) that shipped Core and **Lumogis Search** overlay together without Docker. **ADR 093** / **ADR 094** demoted this in favour of **Lumogis Server** + thin clients; the fused profile is retained in-tree for reference only and is **quarantine-bound ([ADR 100](decisions/100-lum-491-fused-hub-cleanup.md), LUM-491)**. The **shared sidecar supervisor** (`apps/lumogis-server/src-tauri/src/bundled/supervisor.rs`, LUM-396) now powers **Lumogis Server**, not a separate stack. **As of LUM-466 (Phase 1, Linux x64 — [ADR 093](decisions/093-lum-466-core-debundle-delivery-model.md)), Core is staged as a full on-disk `python-build-standalone` venv installing the complete `orchestrator/requirements.txt` (CPU-only `torch` + the `sentence-transformers` / BGE reranker), launched via a thin `orchestrator` sidecar shim that `exec`s the staged venv python — so the native Core install (Lumogis Server) reaches reranker parity with Docker by construction.** The earlier LUM-460 model — a PyInstaller Core sidecar freezing **`orchestrator/requirements-core.txt`** only (no BGE stack) — is superseded on Linux x64 and remains the reference for not-yet-migrated targets; Docker/Compose always installs the full profile via layered **`orchestrator/requirements.txt`** (`-r requirements-core.txt` + BGE extension). Maintainer build chains stage the venv → Tauri release (`make server-stage-core-venv` for Server; legacy `make hub-build-bundled` for the fused profile); CI runs `check-core-venv-size.sh` size + import smoke guards — see [ADR 093](decisions/093-lum-466-core-debundle-delivery-model.md) and [ADR 092](decisions/092-lum-460-slim-pyinstaller-bundle.md). **Supply-chain hardening (LUM-470, LUM-480 — [ADR 097](decisions/097-lum-470-pip-dependency-hash-pinning.md)):** the staged Core venv installs from per-triple **pip hash locks** (`orchestrator/locks-bundled/<triple>.lock.txt`, CPU `torch` two-step install) and verifies the **python-build-standalone CPython tarball** against a committed **`.pbs-sha256`** pin before extract; regen via `make -f Makefile.server.mk server-compile-core-lock` and `server-refresh-pbs-pin` respectively (private build artefacts, stripped from public export). **Legacy fused-only behaviour (not Persona C steady state):** **Persona-aware Settings (LUM-464)** on the fused Hub profile; **first launch** windowed setup wizard → **`enter_overlay_mode`** (Hub-only Tauri command) for frameless overlay chrome; **subsequent launches** cold-start into overlay mode (hidden until hotkey on X11; **Wayland** show-focused-once fallback). **OS system tray (LUM-457):** toggle + menu on the fused profile; closing the overlay window **hides** rather than quits. See **`apps/lumogis-server/README.md`** (bundled profile section).

**Legacy admin:** FastAPI **root-mounted** pages (`/dashboard`, `/settings`, `/graph/*`, `/backup`, …) still exist and are linked from older UX; **full replacement** of that SPA by Lumogis Web is **deferred**. **`GET/PUT /settings`** (admin JWT) exposes **`ingest_paths`** (effective host paths), **`pending_ingest_paths`**, **`restart_required`**, and **`paperless_configured`** (boolean only — no Paperless URL). **`filesystem_root`** / **`pending_filesystem_root`** are **removed** (LUM-397). PUT accepts a **list**: index **0** = host path (written to **`FILESYSTEM_ROOT`** and **`INGEST_PATHS_HOST`**); indices **1..n** = extra folders (validated as existing directories inside the orchestrator — typically under **`/host/...`** after copying **`docker-compose.override.yml.<os>`** to **`docker-compose.override.yml`** and restarting). With **two or more** paths, the API returns **`ingest_compose_snippet`** (copyable override YAML) and, when **`/project/.env`** is writable, may auto-write **`docker-compose.override.yml`**, append **`:docker-compose.override.yml`** to **`COMPOSE_FILE`** (colon-separated for **stack-control**), and set container paths **`/data-1`**, **`/data-2`**, … in **`INGEST_PATHS`**. Indexed paths **must not contain spaces** (Compose limitation). Shrinking to a single path removes the managed bind block and unchains the override file when empty. Runtime **search**, **tools**, **MCP read_file**, and **ingest-path watchers** use **`INGEST_PATHS`** (container JSON). **`POST /api/v1/ingest/upload`** (user JWT) queues per-user document push ingest to persistent storage under **`ai-workspace/uploads/`**.

**Roadmap (cross-device Web parent plan — not all shipped):**

| Phase | Status |
| --- | --- |
| **Phase 2** mobile UX | **Shipped** (MVP) (**2A–2D**) |
| **Phase 3** PWA / bounded caching | **Partial** — `clients/lumogis-web/src/pwa/` (**`sw.ts`**, manifest, precache + push boundary); **not** full offline product |
| **Phase 4** Web Push + notifications | **Partial** — subscription APIs + service-worker handling + **unified dispatcher + per-user prefs API + editable `/me/notifications` matrix** shipped (**LUM-93**, ADR 077/098); quiet-hours policy UI (**LUM-144**), full Web Push template coverage (**LUM-28**), inbox (**LUM-174**) still open; **automatic push mirroring every connector/action outcome** not wired |
| **Phase 5** capture | **Partial** — MVP (**QuickCapture** **`/capture`** plus **`/api/v1/captures`** staging APIs); **semantic_search** still **`documents`**-biased versus indexed captures |
| **Phase 6** full Tauri SPA shell (bundle `lumogis-web` + cookie session; LUM-44 programme) | **Deferred** |
| **Lumogis Search overlay** (`clients/lumogis-search/`, LUM-329 / LUM-430) | **Shipped (v0.1, AGPL)** — bearer + keychain; see **`clients/lumogis-search/README.md`** |

---

## 14. APIs and surfaces

- **`/api/v1/*`** — Stable **Lumogis Web** façade (auth, me, admin, notifications subscription API, **captures** CRUD and attachments under **`/api/v1/captures`**, **`POST /api/v1/voice/transcribe`** when speech-to-text is enabled, etc.). OpenAPI: orchestrator `/openapi.json`; committed snapshot `clients/lumogis-web/openapi.snapshot.json`; codegen `npm run codegen` / `make web-codegen`. **Regenerate snapshot** (from repo root): `cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys --out ../clients/lumogis-web/openapi.snapshot.json` (same as `test_api_v1_openapi_snapshot.py`). **CI** additionally runs **`make openapi-breaking-check`** (oasdiff semantic diff vs merge-base / `HEAD~1` after the LUM-94 snapshot gate) — see **CONTRIBUTING.md** (LUM-302).
- **Legacy routes** — `/ask`, `/ingest`, `/search`, OpenAI-compatible **`/v1/*`** paths (used by optional LibreChat and similar clients), root admin pages—still present for compatibility.
- **`/mcp/`** — MCP streamable HTTP (trailing slash matters for some clients). **Cursor (local):** the **`lumogis-mcp`** stdio bridge (`clients/lumogis-mcp/`, `make lumogis-cursor-install`) forwards tool calls to this endpoint without re-declaring tools; **`make test-cursor-integration`** runs the LUM-299 smoke harness (in-process `/mcp/` breadth over `tests/fixtures/coding_bank.json` plus a real stdio subprocess slice) without Docker — see [ADR 137](decisions/137-lum-299-cursor-integration-smoke-test.md); **opt-in tier-2:** **`make prove-cursor-integration-full`** seeds the same fixture into **lumogis-test** Postgres+Qdrant and asserts **`recall` p95 &lt; 200ms** ([LUM-540](https://linear.app/lumogis/issue/LUM-540); `tests/integration/README.md`); direct HTTP transport remains supported for advanced setups (Step 10 in `docs/private/ops/connect-and-verify.md`). Read tools (`memory.*`, `entity.*`, `context.build`) plus **write** tools **`add_memory`**, **`add_entity`**, **`add_relation`** (LUM-291) that persist into the knowledge graph; write tools require a token carrying the **`mcp:write`** scope (`mint()` supports scopes; `NULL` = unrestricted). New memory text lives in `memories`, typed relations in `entity_edges` (Postgres system-of-record, projected to FalkorDB when enabled); both bank-scoped. See [ADR 128](decisions/128-lum-291-mcp-memory-write-surface.md). The write set is completed (LUM-526) by **`forget`** (reversible **soft archive** — sets `valid_until`, never a hard delete), **`update_observation`** (supersede: add new with `metadata.supersedes`, then archive old, history retained), and **`checkpoint`** (a `metadata.kind=checkpoint` session-boundary memory); all three also require **`mcp:write`**. Per-memory hard erasure is an admin concern, kept off the MCP surface (deferred, LUM-529). See [ADR 129](decisions/129-lum-526-mcp-supersede-archive-tools.md). Tokens are minted with a chosen scope (LUM-527): **`POST /api/v1/me/mcp-tokens`** accepts a validated **`scopes`** list (allowlist `mcp:read`/`mcp:write`; unknown values and `[]` → 422; **omitted/`null` ⇒ read-only `["mcp:read"]`** — LUM-531; write requires explicitly sending `mcp:write`), and **Lumogis Web → Me → MCP tokens** offers a **Read-only / Read + write** selector (default least-privilege) plus per-token access display — closing the fail-open default so **no** token minted via the API can write unless write is explicitly granted (the route never mints an unrestricted `NULL` token; `NULL` = unrestricted remains a legacy/internal-only state). See [ADR 130](decisions/130-lum-527-mcp-token-scope-selection.md). **Origin guard (LUM-296):** requests with a non-loopback `Origin` header are rejected with `403 origin not allowed` before bearer evaluation; absent `Origin` (Cursor, curl, stdio) and loopback origins pass — see [ADR 132](decisions/132-lum-296-mcp-origin-dns-rebinding-guard-shipped.md). The **read** surface gains **`recall`** (LUM-295) — fused retrieval over the LUM-291 memory store combining **semantic** (Qdrant), **BM25** (Postgres `tsvector`, migration `041-memories-fts.sql`), **graph** (1-hop `entity_edges`, Postgres-default), and a **temporal** validity filter (`as_of`), merged with Reciprocal Rank Fusion and an optional cross-encoder rerank (`cross-encoder/ms-marco-MiniLM-L-6-v2` via the existing `sentence-transformers` path; env **`RECALL_RERANKER_BACKEND`**/**`RECALL_RERANKER_MODEL`**). Results carry `source_strategies` and `entity_ids`; the temporal filter is enforced Postgres-side at hydration, so `forget`/`update_observation` (LUM-526) are observable (archived memories drop out). Like the other reads, `recall` is **ungated** (no `mcp:write`). See [ADR 133](decisions/133-lum-295-tempr-recall-fusion.md). **Coding-context type registry (LUM-294):** `add_entity` accepts these `entity_type`s — base `PERSON`/`ORG`/`PROJECT`/`CONCEPT` plus coding `CODING_DECISION`/`CODING_CONVENTION`/`COMPONENT`/`FAILURE`/`SESSION`/`TASK`/`LIBRARY`; `add_relation` accepts `DEPENDS_ON`/`PART_OF`/`DECIDED`/`RELATES_TO`/`SUPERSEDES`/`DECIDED_BY`/`IMPLEMENTS`/`REPLACES`/`CAUSED_BY`/`DISCUSSED_IN_SESSION`/`BLOCKED_BY`/`REFERENCES_ISSUE`. Tokens are **`UPPER_SNAKE`** (send `CODING_DECISION`, not `CodingDecision` — the validator upper-cases but does not insert underscores; `decided_by`/`DECIDED_BY` both work, `DecidedBy` does not). The registry is an in-code allowlist (no migration to add a type), and `add_memory`'s LLM relation extraction proposes the coding relations too. `checkpoint` auto-creates a `SESSION` entity per checkpoint. `DECIDED_BY` (subject→decision) coexists with the legacy `DECIDED` (decision→subject). **Bank isolation (LUM-293):** memories and edges are bank-scoped (`coding`, `personal`, `default`); Qdrant uses payload tenancy on `bank` (tenant index); FalkorDB routes each bank to a separate graph via `get_graph_store(bank)`; `recall` accepts `bank="*"` for cross-bank read opt-in; entities remain user-scoped (names visible across banks). See [ADR 138](decisions/138-lum-293-bank-isolation.md).
- **`/events`** — SSE stream.
- **Caddy** — Terminates TLS optional; routes API and events to Core; SPA fallback for `/`.
- **CSRF / Origin** — Cookie-authenticated writes use same-origin assumptions; set `LUMOGIS_PUBLIC_ORIGIN`—`ARCHITECTURE.md`, `.env.example`. In v1, **Bearer**-authenticated writes skip `require_same_origin` by design; narrowing that bypass ships with the `cross_device_lumogis_web` cookie-session programme—see the module docstring in `orchestrator/csrf.py` and [ADR 046](decisions/046-lum-35-fp017-per-user-backup-followups.md).
- **Refresh cookie** — `httpOnly`, `SameSite=Strict`, path scoped under `/api/v1/auth`.

**Route groups (non-exhaustive):**

| Group | Examples |
| --- | --- |
| Auth | `/api/v1/auth/login`, `refresh`, `logout`, `me` |
| Me | `/api/v1/me/*` (profile, connectors, tools catalog, export, password, …); **`POST /api/v1/voice/transcribe`** when STT enabled; **`GET /api/v1/health`** — authed non-admin Ollama/Qdrant/graph snapshot for web degradation banners (**LUM-512**, cached ~10s; [ADR 107](decisions/107-lum-212-211-512-web-ux-loading-errors-health.md)) |
| Admin | `/api/v1/admin/*` (users, diagnostics, user-imports, …); **`GET /api/v1/admin/diagnostics/stack-status`** — runtime-agnostic service matrix + storage + Ollama list (**200** partial snapshot when stores or stack-control are down; admin-only; **LUM-178**) |
| Audit | Action audit via actions routes + `audit_log`-backed admin views |
| Notifications | `/api/v1/me/notifications` (read-only channel status); `/api/v1/me/notification-preferences` (GET/PATCH routing prefs); `/api/v1/admin/notification-tier-policy` (admin tier defaults); `/api/v1/notifications/*` (push subscription plumbing) |
| Tools / capabilities | Tool execution via chat/`run_tool`; catalog via `GET /api/v1/me/tools` |
| Capture | `/api/v1/captures`, `/api/v1/captures/text`, `/api/v1/captures/upload`, `/api/v1/captures/{id}`, attachments, transcribe, index |
| Ingest progress (LUM-511) | `/api/v1/ingest/upload` (202 + `job_id`), `/api/v1/ingest/jobs/{job_id}`, `/api/v1/ingest/batches/{batch_id}`; SSE **`ingest_progress`** on `/api/v1/events` |
| Chat / search / KG | `/v1/chat/completions`, data routes, graph routes when enabled |

---

## 15. Deployment and local development

**Stack:** `docker-compose.yml` — orchestrator, Postgres, Qdrant, Ollama, stack-control, **Caddy**, **lumogis-web**, **`backup`** DR sidecar (LUM-185); optional **LibreChat** profile (legacy-compatible chat); optional FalkorDB / premium / GPU / dev overlays.

**Disaster recovery backup (LUM-185):** A **`backup`** Compose service runs **supercronic** on a schedule (default **`0 3 * * *`** in the container **`TZ`**) and writes verified snapshots under **`BACKUP_HOST_DIR`** (host bind, default **`./ai-workspace/backups`**) → in-container **`/backups/snapshots/YYYYMMDD-HHMMSS/`** with **`manifest.json`**. Stores: Postgres **`pg_dump -Fc`**, per-collection Qdrant REST snapshots, optional FalkorDB **`BGSAVE`** + read-only **`falkordb_data`** mount when the graph overlay is active. Operators use **`make backup`**, **`make backup-verify`**, and interactive **`make restore SNAPSHOT=snapshots/<id> --yes`** (stop orchestrator + lumogis-web before restore). Admin read-only status: **`GET /api/v1/admin/diagnostics/backup-status`** and the Lumogis Web **System status** backup card. Legacy **`POST /backup`** remains a **logical JSON export**, not DR — see **`docs/guides/backup-restore.md`** and [ADR 098](decisions/098-lum-185-backup-restore.md).

**Update mechanism (LUM-187):** migrations are forward-only numbered SQL files in **`postgres/migrations/`** applied idempotently by **`orchestrator/db_migrations.py`** on every container boot (a failed migration halts start and logs **`[migrations] ERROR …`** — never silent). Operators preview pending work with **`make migrate-dry-run`** (read-only), then update with **`make update`** (**`scripts/update/update.sh`** — records per-service image digests from running containers into **`.lumogis-state/previous-images.txt`**, generates **`rollback-compose.override.yml`** for rollback pinning, **`docker compose pull`**, **`up -d`** to restart and apply migrations, then **`/healthz`** plus a migration dry-run and boot-log check via **`scripts/update/common.sh`**). If an update goes bad, **`make rollback`** (**`scripts/update/rollback.sh`**) appends the saved override to **`COMPOSE_FILE`**, re-pins the previously-running image refs, and restarts with the same health + migration gate — it **requires a backup within `LUMOGIS_BACKUP_MAX_AGE_HOURS`** (default 24h, LUM-185) because schema migrations are not reverted; cross-migration data changes need **`make restore`**. Override generation uses **`scripts/update/write_rollback_override.py`** (unit-tested). Both ops scripts prompt unless **`LUMOGIS_ASSUME_YES=1`**. Admin read-only version/update state: **`GET /api/v1/admin/diagnostics/update-status`** (compares running **`__version__`** to the latest GitHub release; fail-soft, no auto-update; tune via **`LUMOGIS_UPDATE_CHECK_ENABLED`** / **`LUMOGIS_UPDATE_REPO`**). Lumogis Web **System status** includes a **Software updates** card (**LUM-524**) that consumes this endpoint (mount-once fetch, per-version dismiss, `make update` guidance — no in-browser update). See [ADR 123](decisions/123-lum-187-update-mechanism.md), [ADR 147](decisions/147-lum-524-admin-update-banner.md), and amendment [ADR 125](decisions/125-lum-187-rollback-pin-migration-gate.md).

**`lumogis-web` image build:** `clients/lumogis-web/Dockerfile` uses **`npm ci`** with **`COPY package.json package-lock.json`** (lockfile-pinned install) and a BuildKit **`~/.npm` cache mount** on the install step for faster repeated builds (reproducibility unchanged — **`docker build --no-cache`** still runs full **`npm ci`** from the lockfile), then copies the OpenAPI snapshot and sources, runs **`npm run codegen`** (after the final **`COPY src`**) and **`npm run build`**. A **`.dockerignore`** keeps generated paths, `node_modules`, and env/test fixtures out of the build context. CI hygiene: **`make web-dockerfile-check`** fails if the Dockerfile drops **`npm ci`**, the lockfile **`COPY`**, or the BuildKit **`# syntax=`** directive. GitHub Actions job **`web-docker-build`** in **`.github/workflows/ci.yml`** runs **`bash -n`** and **ShellCheck** on **`.github/scripts/web-docker-build-paths.sh`**, then builds **`lumogis-web`** from **`docker-compose.yml`** via **`docker/bake-action`** with GitHub Actions BuildKit layer cache on CI runners (local parity: **`make web-docker-build`** → **`docker compose build lumogis-web`**) on pushes to **`main`/`master`** and on PRs when the path contract matches (otherwise it logs a skip line and exits green so the check can be required without stalling docs-only PRs) — see [ADR 048](decisions/048-lumogis-web-docker-build-ci.md). See [ADR 043](decisions/043-lumogis-web-dockerfile-npm-ci.md).

**Optional web Playwright CI (LUM-60):** workflow **`.github/workflows/web-e2e.yml`** brings up a **slim** Compose project (no Ollama) with **`docker-compose.test.yml`** + **`docker-compose.web-e2e-ci.yml`**, then runs **`make web-e2e-prove`** on the runner. Same-repo PRs need label **`ci:run-web-e2e`** after a path-gate hit; fork PRs skip cred-gated steps. Repository secrets **`LUMOGIS_WEB_SMOKE_EMAIL`** / **`LUMOGIS_WEB_SMOKE_PASSWORD`** must match bootstrap admin env for the disposable DB. This job does **not** replace **`make verify-public-rc`**. See **`CONTRIBUTING.md`** § *Optional CI — web Playwright* and [ADR 064](decisions/064-lum-60-web-e2e-ci.md).

**Opt-in Ollama mutation Playwright (LUM-450):** maintainer-only **`make web-e2e-ollama-prove`** against the **full** default Compose stack (includes **Ollama**). Requires bootstrap admin smoke creds; the Make target sets **`LUMOGIS_E2E_EXPECT_ADMIN=1`** and **`LUMOGIS_E2E_EXPECT_OLLAMA=1`**. Hermetic pull-then-delete of **`tinyllama:1.1b`** (override **`LUMOGIS_E2E_OLLAMA_PULL_MODEL`**). Asserts LUM-449 async pull progress UI through Caddy. **Not** part of slim **`web-e2e.yml`** or default **`make web-e2e-prove`**. Phase 2 optional CI and **`verify-public-rc-full`** auto-wire → **LUM-453**. See **`CONTRIBUTING.md`** and [ADR 087](decisions/087-lum-450-playwright-ollama-mutations-e2e.md).

**Pre-built Core + Web (GHCR):** CI publishes **`ghcr.io/lumogis/lumogis-orchestrator`** and **`ghcr.io/lumogis/lumogis-web`** (amd64/arm64) **from `lumogis/lumogis` (public repo) only** — images reflect verified public AGPL source, not intermediate private development state (LUM-225). Published images carry **GitHub-hosted SLSA Level 2 build-provenance attestations** verified with **`gh attestation verify`** — see **`docs/capabilities.md`** (**Verifying image provenance**) and [ADR 049](decisions/049-slsa-artifact-attestations-ghcr.md). Use merge file **`docker-compose.ghcr.yml`** so those two services pull images instead of local **`build:`** — **`COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`**. Overlay merge requires **Docker Compose v2 / buildx toolchain new enough for `build: !reset null`** so inherited **`build`** is dropped (see **`docker-compose.ghcr.yml`** header comments). After the first workflow push, set each new package visibility to **Public** on GitHub (**Packages → package settings**) so anonymous **`docker pull`** works. Pin tags with **`IMAGE_TAG`** (semver from **`docker/metadata-action`** has **no leading `v`**, e.g. **`IMAGE_TAG=1.2.3`**). Maintainers run **`make verify-public-rc`** on private `main` before `/publish-private-main-to-public`; a push to public `main` then triggers the workflow. **`scripts/check-public-export.sh`** (tail of **`verify-public-rc`**) asserts that the export tree still carries the **`openapi-check`** workflow inputs (offline OpenAPI surface) so public contributor PRs retain the same CI contract as private — see [ADR 061](decisions/061-lum-303-public-ci-parity-openapi-check-via-export.md) (LUM-303). Operational records: [ADR 036](decisions/036-docker-image-ci-ghcr.md) (multi-arch + overlay), [ADR 037](decisions/037-ghcr-publish-public-repo-only.md) (trusted source boundary).

**First-run (operators):** step-by-step published-image path, smoke checks, and common failures — **[`docs/deployment/quickstart.md`](deployment/quickstart.md)** (LUM-184).

**Environment:** Copy `.env.example` → `.env`; set `LUMOGIS_PUBLIC_ORIGIN`, `AUTH_ENABLED`, secrets per docs.

**Common commands (verify against root `Makefile`):**

| Command | Use |
| --- | --- |
| `docker compose up -d` | Run stack |
| `make doctor` | **LUM-199** / **LUM-320** / **LUM-338** / **LUM-399** / **LUM-342** / **LUM-341** — host-side stack/config/network checks; read-only by default; optional **`ARGS="--fix"`** (dry-run) / **`ARGS="--fix --apply --yes"`** for safelisted repairs (`compose_up_service`, **`compose_restart_service`** for unhealthy running containers, `ollama_pull_model`, `mkdir_backup_dir`, **`set_env_key`** append-only when **`LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1`**) — see **`scripts/doctor/README.md`**, [ADR 061](decisions/061-lum-199-lumogis-doctor.md), [ADR 065](decisions/065-lum-320-doctor-v2-shell-fix-remediation.md), [ADR 101-lum-342](decisions/101-lum-342-doctor-compose-restart-unhealthy.md). Apply-path audit NDJSON retention: **`LUMOGIS_DOCTOR_AUDIT_MAX_BYTES`** (default 5 MiB) / **`LUMOGIS_DOCTOR_AUDIT_MAX_FILES`** (default 5). Concurrent **`--fix --apply`** on the same **`LUMOGIS_DOCTOR_AUDIT_DIR`** is serialised with non-blocking **`repair.lock`** (**LUM-399**; second run exit **4**). **`flock(1)`** required on apply path. JSON: **`make doctor ARGS="--json"`** (**`jq`** required; **`version: 1`**); **`ARGS="--json --fix"`** emits **`version: 2`**. Security audits opt-in: **`ARGS="--security"`**. When the orchestrator process is up, household operators use Lumogis Web **System status** (`GET /api/v1/admin/diagnostics/stack-status`, **LUM-178**) or authenticated **`GET /admin/health`** for richer JSON — not a parallel in-process doctor CLI — see [ADR 061](decisions/061-lum-199-lumogis-doctor.md) §Revisit (**LUM-322**). |
| `make compose-test-doctor` | **LUM-319** — disposable **`lumogis-test`** stub stack (two-file compose, no RC overlay), then **`make doctor ARGS="--json"`** + minimal **`jq`** shape check; **overwrites `./.env`** from **`config/test.env.example`** — see **`scripts/doctor/README.md`** and [ADR 063](decisions/063-lum-319-doctor-ci-integration.md) |
| `make test` | Host venv: orchestrator + stack-control unit tests |
| `make compose-test` | **No host pytest needed** — installs dev deps in container, runs orchestrator tests against mounted tree |
| `make compose-test-stack-control` | **LUM-326** — stack-control unit tests via Compose (mounts `stack-control/`; Docker only — no host venv or live stack) |
| `make backup` / `make backup-verify` / `make restore` | **LUM-185** — DR snapshot run, verify latest (rewrites manifest), or gated restore via backup sidecar — see **`docs/guides/backup-restore.md`** |
| `make migrate-dry-run` / `make update` / `make rollback` | **LUM-187** — preview pending migrations (read-only); pull + restart + migrate + health/migration gate (override pinning on rollback; backup within 24h required). Version state: `GET /api/v1/admin/diagnostics/update-status`. See [ADR 123](decisions/123-lum-187-update-mechanism.md) / [ADR 125](decisions/125-lum-187-rollback-pin-migration-gate.md). |
| `make compose-test-backup` | **LUM-185** — disposable-compose smoke: one-shot backup + verify (full volume wipe round-trip is **MS-009** manual) |
| `make web-test` | Lumogis Web unit tests (`npm test`) |
| `make web-build` | Production bundle (`npm run build`) |
| `make search-dev` | **LUM-430** — Tauri overlay dev shell (`clients/lumogis-search/`) — requires Rust + Node 20 + OS webview deps |
| `make search-build` | **LUM-430** — release `tauri build` for the AGPL household client (unsigned OK for smoke) |
| `make compose-policy-check` | Compose merge policy (**LUM-43**) — validates **`docker-compose.yml`** + mock overlay + **`docker-compose.ghcr.yml`** (Pass B uses Docker; run via **`make`** so `MOCK_CAPABILITY_SHARED_SECRET` matches CI) |
| `make compose-policy-check-baseline` | Pass A only on **`docker-compose.yml`** (no adversarial overlay) |
| `make compose-policy-check-adversarial` | Pass A negative proof — merges **`docker-compose.test-policy-adversarial.yml`** (expect checker exit **1**, inverted to Make success) |
| `make compose-policy-check-adversarial-envfile` | Same for **`docker-compose.test-policy-adversarial-envfile.yml`** (`env_file` violation). Tracked root fixtures (**LUM-268**); local scratch names match `.gitignore` patterns documented in [ADR 047](decisions/047-compose-policy-adversarial-ci-fixtures.md). |
| `make mock-capability-test` | Mock capability service pytest |
| `make web-codegen-check` | Offline OpenAPI drift check (`python -m scripts.dump_openapi` vs committed `clients/lumogis-web/openapi.snapshot.json`); same behaviour as **`make openapi-check`** |
| `make openapi-breaking-check` | After LUM-94 snapshot parity, **oasdiff** compares the committed OpenAPI snapshot at `HEAD` to the merge-base / `HEAD~1` revision (requires **Go 1.26+** and `go install github.com/oasdiff/oasdiff@v1.15.2`; CI default **`OPENAPI_BREAKING_FAIL_ON=WARN`** since LUM-312 — fails on definite **and** potential-breaking findings; set `=ERR` for the looser gate) — see **CONTRIBUTING.md** §OpenAPI breaking-change contract |
| `make web-dockerfile-check` | Asserts **`lumogis-web` Dockerfile** keeps **`npm ci`** + lockfile **`COPY`** + BuildKit syntax (LUM-224 / LUM-253) |
| `make shellcheck-web-docker-build-paths` | ShellCheck on **`.github/scripts/web-docker-build-paths.sh`** (LUM-274; requires host **`shellcheck`**, e.g. `apt install shellcheck`) |
| `make shellcheck-ci-paths` | ShellCheck on the five remaining CI **`*-paths.sh`** gate scripts (LUM-444; same **`shellcheck`** host requirement) |
| `make web-docker-build` | **`docker compose build lumogis-web`** — same image definition as CI **`web-docker-build`** (CI uses **`docker/bake-action`** + GHA layer cache — LUM-445; requires Docker; from repo root) |

**Orchestrator Python dependencies (LUM-460, LUM-466):** **`orchestrator/requirements-core.txt`** is the base runtime set (Compose image + the legacy/non-Linux PyInstaller freeze path). **`orchestrator/requirements.txt`** layers the optional BGE reranker (`sentence-transformers`) for full Docker/Compose profiles **and — as of LUM-466 (Phase 1, Linux x64) — for the native Core install (Lumogis Server), whose shared staged venv installs the full `requirements.txt` (CPU-only `torch` via `TORCH_CPU_INDEX`)**. New runtime deps belong in **core** first; only reranker-specific pins go in the extension file.

**Why `make compose-test`:** The production orchestrator image does not include pytest; the Makefile installs `requirements-dev.txt` inside a one-off container so CI and contributors without a local venv still get a green unit run.

**Mock capability overlay:** `docker compose -f docker-compose.mock-capability.yml` — see service README.

**Logs:** `docker compose logs orchestrator -f` (or `make logs`); migration messages in orchestrator log—`README.md` “Postgres schema”.

---

## 16. Security model

- **Cloud LLM and connectors:** If you configure a **cloud LLM**, assume **prompt-sized payloads** (including retrieved snippets) may go to that provider. **Connectors** use **your** stored credentials to reach **their** services—network egress is expected when those features run. Neither path implies a bulk export of the whole corpus; still, treat outbound content as sensitive.
- **Trust boundary:** You run Lumogis on a **trusted LAN**; **admin** is powerful (user reset, imports, credential management).
- **Local recovery:** CLI password reset for operators with shell access—[ADR 029](decisions/029-self-hosted-account-password-management.md).
- **Secrets:** In encrypted credential store + env for server secrets; not echoed in diagnostics façades.
- **Refresh invalidation:** Password change/reset clears refresh **JTI**—stolen cookies short-lived after rotation.
- **Capability bearer:** Proves Core is allowed to call **your** capability instance; **`X-Lumogis-User`** labels who the action is **for**—capabilities must not treat it as proof of identity.
- **Internet exposure:** Not the default threat model for v1; reverse-proxy hardening, TLS, rate limits, and **forgot-password** flows are incomplete—treat wide exposure as **extra** hardening work.
- **Responsible disclosure (public tree):** The policy for reporting **undisclosed** vulnerabilities—private channels, coordinated publication, safe-harbour framing, and credit defaults—is in **`SECURITY.md`** at the repo root and duplicated at **`.github/SECURITY.md`** for GitHub’s Security tab. See [ADR 044](decisions/044-coordinated-vulnerability-disclosure-policy.md).
- **Pre-launch audit (LUM-190):** Human-authored findings live under **`docs/security/pre-launch-audit-2026.md`**. CI exposes a path-gated **`security-audit`** job (`.github/workflows/ci.yml`) that runs **`make audit-local`** (blocking) plus advisory **Bandit**; local parity via **`make bandit-check`**. Methodology record: [ADR 060](decisions/060-lum-190-pre-launch-security-audit-methodology.md).

---

## 17. Current roadmap and status

**Status as of 2026-05-11:** Cross-device **Phase 2–5 MVP** work is reflected in-repo; **§13** and **§17** capture remaining gaps (generic Web Push from arbitrary actions, capture versus document search parity, optional CI coverage).

| Area | Status | Notes |
| --- | --- | --- |
| Remediation Phases 0–5 (platform) | **Sufficiently complete** to pause | Phase 4 household façades; Phase 5 capability scaffolding—closeout reviews |
| Remediation Phase 6 | **Deferred** | Marketplace / mTLS / sandbox—not started |
| Admin / Me shell (child plan) | **Complete** (product) | Optional automated browser regression coverage still thin |
| Password management foundation | **Shipped** | [ADR 029](decisions/029-self-hosted-account-password-management.md) |
| Admin user import/export UI | **Shipped** | Admin → Users |
| Cross-device Web Phase 0–1 | **Shipped** | v1 façade + Caddy same-origin |
| Cross-device Web **Phase 2** | **Shipped** (MVP) | See **§13** |
| Cross-device Web **Phases 3–5** (parent) | **Partial** — MVP shipped | PWA spine + Web Push MVP + Capture/QuickCapture—see **§13**; follow-ups remain |
| Legacy admin replacement | **Deferred** | Link-out still |
| Email forgot-password | **Deferred** | SMTP / abuse scope |

---

## 18. How to extend Lumogis safely (contributors)

**Do:**

- Put business logic in **`services/`**; keep **`routes/`** thin.
- Use **`ports/`** and **`config.get_*()`** from services—never **`from adapters`** in services or routes.
- Respect the **plugin import** allow-list—[`plugin-imports.md`](architecture/plugin-imports.md).
- Register connectors in **`connectors/registry.py`**; use **ToolCatalog** for visibility; use **ToolExecutor** / capability proxy for OOP tools.
- Add tests; update **OpenAPI snapshot** when changing `/api/v1/*`.
- Record architecture shifts in **ADRs** via project skills (`/explore`, `/verify-plan`).

**Do not:**

- Expose secrets in logs, diagnostics, or Web JSON.
- Bypass Core **permission** or **Ask/Do** policy from a client or capability.
- Give capability containers **Postgres/Qdrant** credentials.
- Slip **Phase 6 marketplace** assumptions into household code paths.
- Introduce new **`user_id="default"`** literals in hot paths—grep gates exist.

See [`CONTRIBUTING.md`](../CONTRIBUTING.md). **§19** below groups real extension work into **five practical families** and only then maps the lower-level building blocks—so you can choose a path without memorising every construct on day one.

---

## 19. Extending Lumogis without getting lost

Lumogis is **modular** and has **real moving parts**—services, routes, adapters, connectors, actions, signals, plugins, capabilities, MCP. That is intentional. It also means the docs can look like a long checklist of equal options, which oversells how often you need each one.

In practice, **most** changes fall into **five families** below. The underlying constructs stay important, but they are **building blocks**, not **eleven separate front doors** for every newcomer. Start from the family that matches your goal; reach for adapters, plugins, or MCP when the family (and **§19.4**) says so.

### 19.1 The simple decision tree

```text
Do you want a new UI or app?
  → Add a client.

Do you want Lumogis to talk to an outside service?
  → Add an integration.

Do you want new logic inside Core?
  → Add Core behaviour.

Is the feature heavy, optional, separately packaged, or premium?
  → Add an optional capability.

Do you only want an external agent to call an existing Core function?
  → Expose it through MCP.
```

**Rule:** Do not introduce a new architectural category unless one of these five families cannot express the need.

### 19.2 The five extension families

| Family | Use when | Usually touches | Avoid |
| --- | --- | --- | --- |
| **Add a client** | Building a new UI, mobile shell, desktop wrapper, script, or HTTP consumer. | `/api/v1/*`, OpenAPI snapshot/codegen, auth, maybe Caddy. | Direct DB access, duplicating Core policy, sending secrets to a third-party server. |
| **Add an integration** | Lumogis must talk to another service (calendar, notification, LLM provider, storage, …). | Connector registry, credential services, permission labels, maybe tools/actions. | Storing credentials in random tables, bypassing Ask/Do, exposing secrets. |
| **Add Core behaviour** | Adding business logic inside Lumogis Core. | `services/`, `routes/`, adapters, actions, signals, tests; sometimes Compose + Caddy for a new daemon the stack needs. | Routes importing adapters, business logic stuffed in route handlers, missing `user_id` scoping. |
| **Add an optional capability** | The feature is heavy, separately packaged, optional, premium, or should not live inside Core. | Capability manifest, health endpoint, `/tools/{name}`, bearer config, ToolCatalog. | Shared Core Postgres/Qdrant credentials, trusting `X-Lumogis-User` as auth, marketplace assumptions. |
| **Expose through MCP** | External MCP agents need a **curated** Core function. | `mcp_server.py` and MCP tests. | Treating MCP as the primary tool registry or plugin system. |

### 19.3 How the lower-level constructs fit

| Construct | Role |
| --- | --- |
| **Service** | Business logic inside Core |
| **Route** | HTTP entrypoint |
| **Adapter** | Talks to infrastructure behind a port |
| **Connector** | External integration identity + credentials |
| **Action** | Audited side effect with Ask/Do |
| **Tool** | LLM-callable structured interface |
| **Signal** | External or scheduled event input |
| **Routine** | Repeated or automated behaviour |
| **Plugin** | In-process extension point |
| **Capability** | Out-of-process optional service |
| **MCP** | Curated external-agent transport |

These are **building blocks**. Most features use **several** of them. New contributors should start from the **five families** in **§19.2**, not from this raw list.

### 19.4 Recommended defaults

- **New UI:** Use `/api/v1/*`; do **not** talk to Postgres or Qdrant directly.
- **New internal logic:** Service first, thin route second.
- **New external credentials:** Connector + encrypted credential tier ([ADR 018](decisions/018-per-user-connector-credentials.md), [027](decisions/027-credential_scopes_shared_system.md)).
- **New side effects:** Action + Ask/Do + audit ([ADR 006](decisions/006-ask-do-safety-model.md)).
- **New LLM-callable behaviour:** `ToolSpec` backed by service or action.
- **Heavy or optional features:** Prefer a **capability**, not a plugin ([ADR 010](decisions/010-ecosystem-plumbing.md); graph boundary **[ADR 002](decisions/002-graph-store-falkordb.md)**).
- **Agent interoperability:** MCP only if the tool is **intentionally** part of the MCP surface ([ADR 017](decisions/017-mcp-token-user-map.md)).
- **New stack daemon:** Compose overlay (or base file), wire Core through an adapter and `config.py`—not ad hoc connection strings in random modules.

### 19.5 Plugin vs capability

#### Plugin

- Runs **inside** Core.
- Use **sparingly**—good for first-party or internal hooks.
- Shares Core’s process and failure surface ([ADR 005](decisions/005-plugin-boundary.md)).

#### Capability

- Runs **outside** Core.
- Preferred for optional, heavy, or premium features.
- Talks to Core over HTTP; **no** direct Core DB/Qdrant access.

**Rule:** For new optional features, **prefer a capability** over an in-process plugin unless there is a **strong** reason to run inside Core.

### 19.6 MCP is not another architecture pillar

MCP is **transport**. It exposes a **curated subset** of Core functions to external agents. It is **not** the primary plugin system, **not** the main tool registry, and **not** a way to bypass Core permissions, credentials, or audit. Heavy or stateful tools should usually be implemented as Core **services/actions** or **capabilities** first, then **optionally** exposed to MCP ([`tool-vocabulary.md`](architecture/tool-vocabulary.md)).

### 19.7 Practical examples

#### Example 1 — Add Google Drive connector

- **Family:** Add an integration.
- **Touches:** Connector registry, credential form, permission mode; possibly a tool or action that calls the API.

#### Example 2 — Add mobile app

- **Family:** Add a client.
- **Touches:** `/api/v1/*`, auth, OpenAPI snapshot/codegen; client talks only to your Core.

#### Example 3 — Add OCR service

- **Family:** Add optional capability if it is heavy or a separate container.
- **Touches:** Manifest, `/health`, `POST /tools/…`, bearer trust; optional ToolCatalog exposure.

#### Example 4 — Add weekly digest routine

- **Family:** Add Core behaviour.
- **Touches:** Signal or schedule, routine/action wiring, audit, notification path.

### 19.8 Links for detailed implementation

- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — dev setup, codegen, adapter walkthrough, plugin how-to, **changelog / PR CI gate** (**§Changelog**)
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) — pillars, boundaries, routing  
- [`tool-vocabulary.md`](architecture/tool-vocabulary.md) — tools, capabilities, MCP wording  
- [`plugin-imports.md`](architecture/plugin-imports.md) — what plugins may import  
- [`extending/capability-contract-v1.md`](extending/capability-contract-v1.md) — normative HTTP capability author guide (Extension Contract v1; **LUM-241**)  
- **ADRs:** [005](decisions/005-plugin-boundary.md), [006](decisions/006-ask-do-safety-model.md), [010](decisions/010-ecosystem-plumbing.md), [169](decisions/169-lum-41-capability-invoke-contract-v1.md), [002](decisions/002-graph-store-falkordb.md), [012](decisions/012-family-lan-multi-user.md), [018](decisions/018-per-user-connector-credentials.md), [024](decisions/024-per-user-connector-permissions.md), [027](decisions/027-credential_scopes_shared_system.md), [028](decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md)

---

## 20. Glossary

| Term | Meaning |
| --- | --- |
| **Core** | Orchestrator + its stores and policies |
| **Orchestrator** | FastAPI app in `orchestrator/` |
| **Client** | HTTP UX surface (**Lumogis Web** first; optional LibreChat; MCP; scripts) |
| **Connector** | Named integration id for credentials + permissions |
| **Credential tier** | Per-user / household / system scope ([ADR 027](decisions/027-credential_scopes_shared_system.md)) |
| **Tool** | LLM-callable `ToolSpec` |
| **Action** | Audited registry operation with Ask/Do |
| **Signal** | Polled or received event input |
| **Routine** | Scheduled elevation of trusted actions |
| **Plugin** | In-process extension |
| **Capability** | Out-of-process HTTP service with manifest |
| **MCP** | Agent transport at `/mcp/` |
| **KG** | Knowledge graph (optional FalkorDB-backed) |
| **Qdrant** | Vector store |
| **Postgres** | Relational metadata + audit |
| **Ask/Do** | Safety execution modes |
| **Audit log** | Durable record of actions/tools |
| **Household LAN** | Primary deployment trust model |
| **OOP capability** | Out-of-process tool provider over HTTP |

---

## 21. Further reading

- **[§19](#19-extending-lumogis-without-getting-lost)** — extension work grouped into five families (not an exhaustive per-construct checklist).  
- [`docs/extending/capability-contract-v1.md`](extending/capability-contract-v1.md) — HTTP capability author guide (Extension Contract v1)  
- [`README.md`](../README.md) — install, stack, optional LibreChat profile notes  
- **[Capabilities](../capabilities.md)** — concise shipped-capability overview for contributors and self-hosters  
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) — pillars, Caddy routing, MCP, plugins  
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — dev setup, `compose-test`, codegen, **`make changelog-check`** path gate (mirrors `.github/workflows/changelog.yml`)
- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI vs integration / web / KG / browser suites  
- **ADRs:** [005](decisions/005-plugin-boundary.md), [006](decisions/006-ask-do-safety-model.md), [010](decisions/010-ecosystem-plumbing.md), [002](decisions/002-graph-store-falkordb.md), [012](decisions/012-family-lan-multi-user.md), [015](decisions/015-personal-shared-system-memory-scopes.md), [017](decisions/017-mcp-token-user-map.md), [018](decisions/018-per-user-connector-credentials.md), [019](decisions/019-structured-audit-logging.md), [024](decisions/024-per-user-connector-permissions.md), [026](decisions/026-llm-provider-keys-per-user.md), [027](decisions/027-credential_scopes_shared_system.md), [028](decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md), [029](decisions/029-self-hosted-account-password-management.md), [041](decisions/041-jwt-access-token-revocation-multi-device-sessions.md)
- [Tool vocabulary](architecture/tool-vocabulary.md)  
- [Plugin imports](architecture/plugin-imports.md)  
- [`clients/lumogis-web/README.md`](../clients/lumogis-web/README.md)  
- [`services/lumogis-graph/README.md`](../services/lumogis-graph/README.md)  
- [`services/lumogis-mock-capability/README.md`](../services/lumogis-mock-capability/README.md)  

---

*This manual is descriptive documentation; it is not a warranty of feature completeness. For version-specific behaviour, rely on the codebase, OpenAPI, and ADRs.*
