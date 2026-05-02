# Lumogis Reference Manual

<!-- markdownlint-disable MD013 -->

**Slug:** `lumogis_reference_manual`  
**Audience:** Household operators, curious readers, and contributors.  
**Scope:** Describes Lumogis **as of** consolidation after cross-device Lumogis Web Phase 0/1, Admin & Me shell closure, self-hosted architecture remediation Phases 0–5 (household surfaces + capability scaffolding), password-management foundation, admin user import/export UI, and extraction of the **parent** Web Phase 2 (mobile UX) plan.  
**Authority:** Prefer **closeout reviews** and **[Web roadmap reconciliation](architecture/lumogis-web-roadmap-reconciliation-after-remediation.md)** over older plan prose when they disagree.

Last reviewed: 2026-05-02  
Verified against commit: 98f02b1

**Code cross-check (spot audit):** Key claims were traced to `orchestrator/config.py` (`get_tool_catalog_enabled`), `orchestrator/loop.py` + `services/unified_tools.py` (tool-list merge + teardown), `services/capability_http.py` (`graph_query_tool_proxy_call` / `{"input": …}`), `services/execution.py` (`tool.execute.capability`), `routes/auth.py` (`REFRESH_COOKIE_PATH = "/api/v1/auth"`), `docker/caddy/Caddyfile` path table, `postgres/migrations/016-per-user-connector-permissions.sql` (per-user `connector_permissions`), `rg` for `from adapters` under `orchestrator/services/` and `orchestrator/routes/` (no matches), `docker-compose.yml` (no mock-capability service), and `clients/lumogis-web` routes under `/me/*` and `/admin/*`. **§19** frames extension work as **five practical families** (plus how lower-level pieces compose); it aligns with `ARCHITECTURE.md` / `CONTRIBUTING.md`, not a parallel architecture.

**Important — two different “Phase 4 / Phase 5” programmes:**

| Programme | “Phase 4” means | “Phase 5” means |
| --- | --- | --- |
| **Self-hosted architecture remediation** ([remediation plan](architecture/lumogis-self-hosted-platform-remediation-plan.md)) | Household-control JSON façades + Web read-only views (e.g. `/api/v1/me/tools`, diagnostics) | Optional **capability** scaffolding (discovery, OOP tool bridge, mock service, audit fan-in) |
| **Cross-device Lumogis Web** (parent plan files may exist only on maintainer checkouts — *not in this repository*; see [Web roadmap reconciliation](architecture/lumogis-web-roadmap-reconciliation-after-remediation.md)) | **Web Push** + background approvals (not the same as remediation Phase 4) | **Capture-from-anywhere** (not the same as remediation Phase 5) |

This manual uses **remediation** vs **cross-device Web** explicitly to avoid confusion.

## How to read this manual

| Path | Who | Where to start |
| --- | --- | --- |
| **Household / operator** | You run Lumogis at home and want the big picture—what it is, what the parts do, how to deploy safely. | **§1–§3** (what / principles / components), **§13–§16** (Lumogis Web, APIs, deployment, security), **§17** (what is shipped vs planned). |
| **Technical contributor** | You are changing Core, Web, or integrations and need boundaries and patterns. | **§4–§5** (mental model, pillars), **§18–§19** (rules + extension families), **`ARCHITECTURE.md`** and **`CONTRIBUTING.md`** linked in **§21**. |
| **Roadmap / status** | You need to know what is done, what is next, and how “Phase 4/5” differs between programmes. | **Phase table** at the top of this page, **§17**, and **[Web roadmap reconciliation](architecture/lumogis-web-roadmap-reconciliation-after-remediation.md)**. |

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
| **Optional capabilities** | Heavy or isolated features can run **beside** Core, not inside its DB. | HTTP manifest at `GET /capabilities`, bearer trust—[ADR 010](decisions/010-ecosystem-plumbing.md), [011](decisions/011-lumogis-graph-service-extraction.md). |
| **Agentic Core direction** | Future agents remain bounded roles under Core policy, not autonomous authority. | Planning baseline: [Agentic Core](architecture/agentic_core.md); any draft ADR text may exist only on maintainer checkouts. |
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
| **MCP surface** | Streamable HTTP tools for external agents | MCP clients | `/mcp/` on Core |
| **Clients** | Any HTTP consumer of Core | Humans + automation | **Lumogis Web** (primary household UI), optional LibreChat, curl, MCP |

---

## 4. Mental model: Core, clients, connectors, tools, capabilities

Aligned with [`docs/architecture/tool-vocabulary.md`](architecture/tool-vocabulary.md). For each term: **easy explanation**, then **technical definition**.

| Term | Plain English | Technical |
| --- | --- | --- |
| **Core** | The brain on your box: policies, storage, and execution. | The orchestrator process and its adapters—Postgres/Qdrant/Ollama/etc. |
| **Client** | A program that talks to Core over HTTP and shows UX. | **Lumogis Web** (household + admin); optional **LibreChat** (chat-only legacy path); scripts; must stay thin—no direct DB. |
| **Connector** | A named channel for secrets/settings (ntfy, CalDAV, LLM provider, …). | Registered id in `connectors/registry.py`, encrypted rows in `user_connector_credentials`, tiers per [ADR 027](decisions/027-credential_scopes_shared_system.md); `ToolSpec.connector` links tools to permission checks. |
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

- **`users` table** — accounts with argon2id password hashes, `role` (`admin` | `user`), optional `disabled`, refresh token JTI for session rotation—[ADR 012](decisions/012-family-lan-multi-user.md), [029](decisions/029-self-hosted-account-password-management.md).
- **Bootstrap admin** — first user creation when the table is empty and bootstrap env is set (see `.env.example` / ops docs).
- **Authentication** — `/api/v1/auth/*`: login, refresh (httpOnly cookie), logout, `me`. Access JWT for APIs; refresh cookie path `/api/v1/auth`.
- **Why not cloud multi-tenant:** Product target is **household LAN**; hosted multi-tenant is explicitly deferred ([ADR 012](decisions/012-family-lan-multi-user.md), remediation plan §1, portfolio).

**Password management (implemented):**

| Path | Purpose |
| --- | --- |
| Self-service | `POST /api/v1/me/password` — UI on **Settings → Profile** |
| Admin reset | `POST /api/v1/admin/users/{user_id}/password` — **Admin → Users** |
| CLI recovery | From the **`orchestrator/`** directory: `python -m scripts.reset_password` (see `orchestrator/scripts/reset_password.py` docstring)—[ADR 029](decisions/029-self-hosted-account-password-management.md) |

**Deferred:** email / forgot-password / magic links (`lumogis_forgot_password_email_reset` class of work)—requires SMTP and abuse handling.

---

## 7. Credentials and permissions

**Lay view:** Connectors hold the keys Lumogis needs (ntfy, calendars, LLM APIs). You paste them once; the UI does not show them again. Admins can help manage household-level configuration; some secrets are **per user**, some **shared** or **system** tier.

**Technical view:**

- **Per-user / household / system** credential scopes—[ADR 027](decisions/027-credential_scopes_shared_system.md).
- **Storage:** `user_connector_credentials` with **encrypted** payloads; **MultiFernet** allows key rotation—`orchestrator/services/_credential_internals.py`.
- **Connector permissions** (Ask/Do/blocked) — per-user rows; `get_connector_mode` drives catalog **labels** and execution checks—[ADR 024](decisions/024-per-user-connector-permissions.md).
- **`GET /api/v1/me/tools`** exposes **permission_mode** (`ask` / `do` / `blocked` / `unknown`) as **read model** only; granting/changing permissions is elsewhere.

**Examples:** **ntfy** — notification channel connector ([ADR 022](decisions/022-ntfy-runtime-per-user-shipped.md)). **LLM provider keys** — per-user ([ADR 026](decisions/026-llm-provider-keys-per-user.md)). **CalDAV** — [ADR 021](decisions/021-caldav-connector-credentials.md). **MCP tokens** — [ADR 017](decisions/017-mcp-token-user-map.md).

**Security note:** Do not log secrets; diagnostics and façades avoid env dumps and raw ciphertext. Copy-once tokens where the product uses that pattern. Passwords are never shown again after set.

---

## 8. Memory, search, entities, and context

**Lay view:** Lumogis keeps **structured records** in a database and a **semantic index** for “fuzzy” finding. When you chat, it pulls snippets that matter, trims them to a budget, and **only that bundle** may go to an external model if you use one.

**Technical view:**

- **Postgres** — authoritative metadata, sessions, entities tables, audit, signals, etc.
- **Qdrant** — vector (and optional sparse/hybrid) search; **user_id** filter on queries for isolation.
- **Memory scopes** — personal, shared, system—[ADR 015](decisions/015-personal-shared-system-memory-scopes.md).
- **Entity extraction** — stored and linked per ingestion/session flows—see `services/entities.py`, entity ADRs ([014](decisions/014-entity-relations-evidence-dedup.md), etc.).
- **Context building** — retrieval + `context_budget` truncation before LLM calls (`ARCHITECTURE.md`, `services/context_budget.py`).
- **If a cloud LLM is used:** **Composed prompts and retrieved excerpts** (after context budgeting) are sent to the provider—they **may contain private text** from your index. Lumogis does **not** ship the **entire** local corpus or disk image as a bulk upload; what leaves is **what Core assembled for that request**. **Connectors** are separate: they contact **their** external APIs when invoked, by design.

---

## 9. Actions, tools, and audit

**Lay view:** Some operations are safe to run immediately (**Do**); others must wait for your **approval** (**Ask**). Everything important is logged.

**Technical view:**

- **Ask vs Do** — [ADR 006](decisions/006-ask-do-safety-model.md); executor enforces mode; destructive actions stay Ask.
- **Action registry** + **handlers**; **audit_log** append-only—[ADR 019](decisions/019-structured-audit-logging.md).
- **ToolCatalog** — `build_tool_catalog` / `build_tool_catalog_for_user` — read-only inventory with transports (`llm_loop`, `mcp_surface`, `catalog_only`).
- **ToolExecutor** — `execute_inprocess` / `execute_capability_http` with `PermissionCheck` and audit envelope; **OOP** capability calls fan in to `audit_log` (`tool.execute.capability` style rows)—see `tool-vocabulary.md`.
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** — default **`false`** (`config.get_tool_catalog_enabled()` treats only `1` / `true` / `yes` as on). When **false**, the LLM loop does **not** merge OOP capability tools into the live tool list. When **true**, `prepare_llm_tools_for_request` may append healthy, bearer-authenticated capability tools; **`finish_llm_tools_request`** must run after each request (`loop.py` try/finally)—see `services/unified_tools.py`.
- **Fail-closed:** Missing bearer, unhealthy capability, or denied permission should not silently execute OOP tools.

**Flow (simplified):** User asks → Core builds context (retrieval + budget) → LLM may emit tool call → permission / Ask-Do check → `run_tool` / executor → handler or HTTP proxy → **audit_log** (+ structured logs).

---

## 10. Signals and routines

**Signals** — External or scheduled inputs (RSS, page change, calendar, system). Monitors poll; processor scores and persists; plugins can react via hooks.

**Routines** — Scheduled automation elevating trusted **Ask** actions toward **Do** after enough clean approvals (`ROUTINE_ELEVATION_THRESHOLD`).

**Notifications** — Daily digest patterns via ntfy and connector stack; **Web Push** product flows are **not** the same as the read-only **`/me/notifications`** settings façade (see §13 and reconciliation doc).

**Implemented vs planned:** Core signals + digest + routines are **implemented** in the household self-hosted sense. **Cross-device Web Phase 4** (push client + service worker + background approvals) remains **open** per [reconciliation](architecture/lumogis-web-roadmap-reconciliation-after-remediation.md).

---

## 11. Knowledge graph

**Lay view:** An optional “knowledge graph” can track relationships between entities beyond flat search. You do **not** need it for basic chat and RAG.

**Technical view:**

- **In-process vs service:** Graph plugin can run **in-process** or **`GRAPH_MODE=service`** with **lumogis-graph** as the writer—[ADR 011](decisions/011-lumogis-graph-service-extraction.md), [002](decisions/002-graph-store-falkordb.md), graph plugin [007](decisions/007-graph-plugin-architecture.md).
- **FalkorDB** — backing store for KG in service mode; see `docker-compose.premium.yml` / FalkorDB overlays.
- **`query_graph` tool** — When bridged to the service, HTTP body uses **`{"input": <payload>}`** for the KG `QueryGraphRequest` contract; generic capabilities use **flat** JSON bodies—`tool-vocabulary.md`, Phase 5 closeout.
- **KG-specific vs generic:** KG proxy and manifest are the **reference capability**; generic discovery, health, `/tools/{name}`, and bearer env patterns apply to **any** capability.

---

## 12. Optional capability services

**Plain English:** A capability is an **extra program** Lumogis can discover—like a plug-in module, but running in its **own container**, with its **own data** if needed.

**Technical contract:**

- `GET {base}/capabilities` — `CapabilityManifest` (Pydantic model in `orchestrator/models/capability.py`, vendored to `services/lumogis-graph/models/capability.py`).
- `GET {base}/health` — liveness for registry.
- `POST {base}/tools/{tool_name}` — invocation; Core uses bearer tokens (`LUMOGIS_CAPABILITY_BEARER_<SANITIZED_ID>` pattern); **`X-Lumogis-User`** is **attribution**, not authentication of the capability.
- **No shared Core DB credentials** on capability containers—policy in remediation Phase 5.

**Mock capability:** `services/lumogis-mock-capability/` + `make mock-capability-test`; compose overlay `docker-compose.mock-capability.yml` — **not** in default `docker-compose.yml`.

**Status:** Phase 5 **scaffolding** is **sufficient for self-hosted** experiments; **Phase 6** marketplace / signed manifests / mTLS-by-default is **deferred**—[Phase 5 closeout](architecture/phase-5-final-capability-scaffolding-closeout-review.md).

---

## 13. Lumogis Web

**Purpose:** First-party **household** UI: same-origin with Core via Caddy, consumes **`/api/v1/*`** for auth and control surfaces.

**Completed surfaces (representative):**

| Area | Routes / behaviour |
| --- | --- |
| Chat / search / approvals | Core product pages (Phase 1 baseline) |
| **Me** | `/me/profile` (password change), `/me/connectors`, `/me/permissions`, `/me/llm-providers`, `/me/mcp-tokens`, `/me/notifications`, `/me/export`, `/me/tools-capabilities` |
| **Admin** | `/admin/users` (import/export, password reset), `/admin/connector-credentials`, `/admin/connector-permissions`, `/admin/mcp-tokens`, `/admin/audit`, `/admin/diagnostics` |

**Password management** — shipped per [ADR 029](decisions/029-self-hosted-account-password-management.md).  
**Admin import/export** — inventory + dry-run/real import via `/api/v1/admin/user-imports`; per-user export via `/api/v1/me/export` with `target_user_id`—see [`clients/lumogis-web/README.md`](../clients/lumogis-web/README.md).

**Legacy admin:** FastAPI **root-mounted** pages (`/dashboard`, `/settings`, `/graph/*`, `/backup`, …) still exist and are linked from older UX; **full replacement** of that SPA by Lumogis Web is **deferred**.

**Roadmap (cross-device Web parent plan — not all shipped):**

| Phase | Status |
| --- | --- |
| **Phase 2** mobile UX | **Next** recommended chunk—[extracted plan](architecture/cross-device-web-phase-2-mobile-ux-plan.md) (2A–2D) |
| **Phase 3** PWA / bounded caching | **Open** (no `src/pwa/` tree as of reconciliation) |
| **Phase 4** Web Push + background approvals | **Open** (server routes may exist; **client** SW/opt-in **not** done—do not conflate with `/me/notifications` façade) |
| **Phase 5** capture | **Open** (stubs; different from remediation Phase 5) |
| **Phase 6** Tauri stub | **Deferred / stub** |

---

## 14. APIs and surfaces

- **`/api/v1/*`** — Stable **Lumogis Web** façade (auth, me, admin, notifications subscription API, captures stub, etc.). OpenAPI: orchestrator `/openapi.json`; committed snapshot `clients/lumogis-web/openapi.snapshot.json`; codegen `npm run codegen` / `make web-codegen`. **Regenerate snapshot** (from repo root): `cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys --out ../clients/lumogis-web/openapi.snapshot.json` (same as `test_api_v1_openapi_snapshot.py`).
- **Legacy routes** — `/ask`, `/ingest`, `/search`, OpenAI-compatible **`/v1/*`** paths (used by optional LibreChat and similar clients), root admin pages—still present for compatibility.
- **`/mcp/`** — MCP streamable HTTP (trailing slash matters for some clients).
- **`/events`** — SSE stream.
- **Caddy** — Terminates TLS optional; routes API and events to Core; SPA fallback for `/`.
- **CSRF / Origin** — Cookie-authenticated writes use same-origin assumptions; set `LUMOGIS_PUBLIC_ORIGIN`—`ARCHITECTURE.md`, `.env.example`.
- **Refresh cookie** — `httpOnly`, `SameSite=Strict`, path scoped under `/api/v1/auth`.

**Route groups (non-exhaustive):**

| Group | Examples |
| --- | --- |
| Auth | `/api/v1/auth/login`, `refresh`, `logout`, `me` |
| Me | `/api/v1/me/*` (profile, connectors, tools catalog, export, password, …) |
| Admin | `/api/v1/admin/*` (users, diagnostics, user-imports, …) |
| Audit | Action audit via actions routes + `audit_log`-backed admin views |
| Notifications | `/api/v1/me/notifications` (read façade); `/api/v1/notifications/*` (push subscription plumbing) |
| Tools / capabilities | Tool execution via chat/`run_tool`; catalog via `GET /api/v1/me/tools` |
| Chat / search / KG | `/v1/chat/completions`, data routes, graph routes when enabled |

---

## 15. Deployment and local development

**Stack:** `docker-compose.yml` — orchestrator, Postgres, Qdrant, Ollama, stack-control, **Caddy**, **lumogis-web**; optional **LibreChat** profile (legacy-compatible chat); optional FalkorDB / premium / GPU / dev overlays.

**Environment:** Copy `.env.example` → `.env`; set `LUMOGIS_PUBLIC_ORIGIN`, `AUTH_ENABLED`, secrets per docs.

**Common commands (verify against root `Makefile`):**

| Command | Use |
| --- | --- |
| `docker compose up -d` | Run stack |
| `make test` | Host venv: orchestrator + stack-control unit tests |
| `make compose-test` | **No host pytest needed** — installs dev deps in container, runs orchestrator tests against mounted tree |
| `make web-test` | Lumogis Web unit tests (`npm test`) |
| `make web-build` | Production bundle (`npm run build`) |
| `make mock-capability-test` | Mock capability service pytest |
| `make web-codegen-check` | Drift check vs live OpenAPI (orchestrator must be up) |

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

---

## 17. Current roadmap and status

**Status as of 2026-04-26:** The table below reflects repository and closeout docs at that date; prefer **§21** links and live code for drift after that.

| Area | Status | Notes |
| --- | --- | --- |
| Remediation Phases 0–5 (platform) | **Sufficiently complete** to pause | Phase 4 household façades; Phase 5 capability scaffolding—closeout reviews |
| Remediation Phase 6 | **Deferred** | Marketplace / mTLS / sandbox—not started |
| Admin / Me shell (child plan) | **Complete** (product) | Optional CI e2e (`FP-047`) still open |
| Password management foundation | **Shipped** | [ADR 029](decisions/029-self-hosted-account-password-management.md) |
| Admin user import/export UI | **Shipped** | Admin → Users |
| Cross-device Web Phase 0–1 | **Shipped** | v1 façade + Caddy same-origin |
| Cross-device Web **Phase 2** | **Next** | Mobile UX—[plan](architecture/cross-device-web-phase-2-mobile-ux-plan.md) |
| PWA / Web Push / Capture (parent 3–5) | **Open** | Distinct from remediation 4/5 |
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
- **Heavy or optional features:** Prefer a **capability**, not a plugin ([ADR 010](decisions/010-ecosystem-plumbing.md), [011](decisions/011-lumogis-graph-service-extraction.md)).
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

- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — dev setup, codegen, adapter walkthrough, plugin how-to  
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) — pillars, boundaries, routing  
- [`tool-vocabulary.md`](architecture/tool-vocabulary.md) — tools, capabilities, MCP wording  
- [`plugin-imports.md`](architecture/plugin-imports.md) — what plugins may import  
- **ADRs:** [005](decisions/005-plugin-boundary.md), [006](decisions/006-ask-do-safety-model.md), [010](decisions/010-ecosystem-plumbing.md), [011](decisions/011-lumogis-graph-service-extraction.md), [012](decisions/012-family-lan-multi-user.md), [018](decisions/018-per-user-connector-credentials.md), [024](decisions/024-per-user-connector-permissions.md), [027](decisions/027-credential_scopes_shared_system.md), [028](decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md)  

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
- [`README.md`](../README.md) — install, stack, optional LibreChat profile notes  
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) — pillars, Caddy routing, MCP, plugins  
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — dev setup, `compose-test`, codegen  
- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI vs integration / web / KG / browser suites  
- **ADRs:** [005](decisions/005-plugin-boundary.md), [006](decisions/006-ask-do-safety-model.md), [010](decisions/010-ecosystem-plumbing.md), [011](decisions/011-lumogis-graph-service-extraction.md), [012](decisions/012-family-lan-multi-user.md), [015](decisions/015-personal-shared-system-memory-scopes.md), [017](decisions/017-mcp-token-user-map.md), [018](decisions/018-per-user-connector-credentials.md), [019](decisions/019-structured-audit-logging.md), [024](decisions/024-per-user-connector-permissions.md), [026](decisions/026-llm-provider-keys-per-user.md), [027](decisions/027-credential_scopes_shared_system.md), [028](decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md), [029](decisions/029-self-hosted-account-password-management.md)  
- [Self-hosted remediation plan](architecture/lumogis-self-hosted-platform-remediation-plan.md)  
- [Phase 4 household closeout](architecture/phase-4-household-control-surface-closeout-review.md)  
- [Phase 5 capability closeout](architecture/phase-5-final-capability-scaffolding-closeout-review.md)  
- [Web roadmap reconciliation](architecture/lumogis-web-roadmap-reconciliation-after-remediation.md)  
- [Cross-device Web Phase 2 (mobile UX) plan](architecture/cross-device-web-phase-2-mobile-ux-plan.md)  
- [Tool vocabulary](architecture/tool-vocabulary.md)  
- [Plugin imports](architecture/plugin-imports.md)  
- [`clients/lumogis-web/README.md`](../clients/lumogis-web/README.md)  
- [`services/lumogis-graph/README.md`](../services/lumogis-graph/README.md)  
- [`services/lumogis-mock-capability/README.md`](../services/lumogis-mock-capability/README.md)  
- **Plans (historical context):** Long-form Web plans may exist only on maintainer checkouts *(not tracked in this repository)* — for shipped intent see [`architecture/lumogis-web-roadmap-reconciliation-after-remediation.md`](architecture/lumogis-web-roadmap-reconciliation-after-remediation.md), [`architecture/cross-device-web-phase-2-mobile-ux-plan.md`](architecture/cross-device-web-phase-2-mobile-ux-plan.md), and ADRs  

---

*This manual is descriptive documentation; it is not a warranty of feature completeness. For version-specific behaviour, rely on the codebase, OpenAPI, and ADRs.*
