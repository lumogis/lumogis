# Capabilities

> Lumogis is a self-hosted household AI platform: Core indexes and retrieves your material locally, applies policy-gated tools and actions, and optionally calls configured cloud models with composed prompts and retrieved excerpts—not a bulk upload of your archive.

## Contents

- [Core Platform](#core-platform)
- [Knowledge Graph](#knowledge-graph)
- [Memory & Entities](#memory--entities)
- [Capture & Voice](#capture--voice)
- [Lumogis Web](#lumogis-web)
- [Search & Retrieval](#search--retrieval)
- [Auth / Users / Credentials](#auth--users--credentials)
- [MCP & Tool Catalog](#mcp--tool-catalog)
- [Mobile / Offline / Fallback](#mobile--offline--fallback)
- [Capabilities / Plugins](#capabilities--plugins)
- [Agentic Delivery](#agentic-delivery)
- [Deployment](#deployment)

---

## Core Platform

Core is the FastAPI orchestrator: HTTP APIs, business logic, optional in-process plugins, signals, actions, Postgres metadata, Qdrant vectors, and optional out-of-process capability services behind the same deployment story.

- Default Compose runs Core with Postgres, Qdrant, Ollama, Lumogis Web, Caddy (same-origin routing to the SPA and Core APIs), and stack-control.
- Ingestion and indexing pipeline feed semantic search; sessions and related metadata live in Postgres.
- **Filesystem inbox auto-ingest (LUM-330):** drop supported files into **`ai-workspace/inbox/`** (container path **`/workspace/inbox`**); configurable **`LUMOGIS_INBOX_*`** modes (**`event`**, **`poll`**, **`off`**), write-stability before ingest, poll fallback for unreliable bind mounts, and **`ai-workspace/quarantine/`** for terminal failures — see **[ADR 070](decisions/070-lum-330-folder-watch-inbox.md)** and **`docs/LUMOGIS_REFERENCE_MANUAL.md`**.
- **Multi-path ingest compose binds (LUM-401):** admin **`PUT /settings`** can list multiple **`ingest_paths`**; indices **1..n** use **`orchestrator/compose_ingest_binds.py`** to generate tiered **`docker-compose.override.yml`** snippets (stack restart still required) — see **[ADR 072-lum-401](decisions/072-lum-401-compose-multibind-generator.md)** (ADR number collides with client-only overlay — see **`docs/decisions/`** index).
- **Signals** ingest external or scheduled inputs (feeds, pages, calendars, and similar); monitors poll, score, and persist for downstream use.
- **Routines** automate trusted **Ask** toward **Do** after repeated clean approvals (threshold from configuration).
- Notifications include daily digest patterns via connectors such as ntfy alongside other notification paths. A unified in-process dispatcher, per-user routing preferences, and channel adapters are **decided** in **[ADR 077](decisions/077-lum-189-notification-architecture.md)** (**LUM-189**; implementation programme **LUM-93 → LUM-144 → LUM-28 → LUM-174** — **not shipped** in core yet).
- Server-sent events are available at **`/events`** for live streams.
- Legacy OpenAI-style **`/v1/*`** routes remain for compatible clients (for example optional LibreChat); Lumogis Web uses the versioned **`/api/v1/*`** façade.

Self-hosters configure the stack via `.env` (see `.env.example`), Compose overlays for optional profiles, and Caddy for TLS and routing.

---

## Knowledge Graph

An optional knowledge graph layer relates extracted entities beyond flat search; basic chat and retrieval do not require it.

- Graph can run **in-process** (premium plugin package present) or in **`service`** mode (premium HTTP KG capability reachable from Core). Falkor-backed stacks are activated only when operators merge the corresponding premium overlays.
- **`GRAPH_MODE`** selects **`disabled`** (fresh default — no graph bootstrap noise), **`inprocess`**, or **`service`** and must be paired with matching premium artefacts. **`GRAPH_MODE=inprocess` without the in-process plugin is not a supported public-core mode**: Core observes the request, falls back to **`disabled`**, and emits exactly one **`graph_mode_fallback`** WARNING with reason **`inprocess_plugin_absent`** — chat and ingest continue without KG wiring.
- In **`service`** mode, Core forwards webhook and context traffic to the graph service; the in-process graph plugin stays inactive so work is not duplicated.
- The **`query_graph`** tool can be bridged to the out-of-process service; **`GRAPH_WEBHOOK_SECRET`** (or an explicit Core opt-in documented for insecure-missing-secret scenarios) is required for that proxy path when **`GRAPH_MODE=service`**—otherwise the bridge stays closed.
- Capability manifests may advertise an operator **`management_url`** for a linked management UI where implemented.

Operators enabling **`service`** mode should coordinate `GRAPH_MODE`, KG base URLs (`KG_SERVICE_URL` / `CAPABILITY_SERVICE_URLS`), shared webhook secrets (`GRAPH_WEBHOOK_SECRET`), and capability discovery using the premium operator guide bundled with their graph overlay.

---

## Memory & Entities

Structured memory and extracted entities sit alongside search so chat can draw on scoped records, not only raw documents.

- Memory uses **personal**, **shared**, and **system** scopes for isolation and household-wide context where configured.
- Entity extraction stores and links entities through ingestion and session flows.
- Context assembly applies a **context budget** before model calls so prompts stay bounded.

---

## Capture & Voice

Surfaces for quick capture and optional speech input complement chat and ingest.

- **`/capture` (QuickCapture)** uses bounded client-side staging (IndexedDB) with explicit sync steps rather than silent server writes from the queue alone.
- **Authenticated capture ledger APIs** under **`/api/v1/captures`** create, list, update, and delete capture rows for the signed-in user; support attachments and capture-linked transcription requests; and expose indexing submission so captures can enter the ingest pipeline. Pending captures stay editable; indexed captures block destructive edits enforced by the API.
- **Semantic search** over captures versus ordinary **`documents`** remains uneven—treat capture-oriented discovery as still catching up to library search.
- **Push-to-talk** transcription via **`POST /api/v1/voice/transcribe`** accepts short audio uploads and returns plain text for an editable field when speech-to-text is enabled (`STT_BACKEND`); **off** by default. A lightweight **`fake_stt`** backend supports tests and scaffolding; production-style deployments use Whisper-class STT in-process or via an optional HTTP sidecar enabled through Compose.

Enable STT only when you accept extra CPU/RAM (and optional GPU) cost; use the STT Compose overlay and environment knobs from operator docs when running a sidecar.

---

## Lumogis Web

The first-party SPA is the primary household UI: chat, search, approvals, and Me/Admin settings on the same origin as Core via Caddy.

- **Me**: profile (including password change), connectors, connector permissions, LLM providers, MCP tokens, notifications, export, tools/capabilities overview.
- **Conversation history (LUM-162):** browse, continue, and delete past conversations with multi-store purge APIs — see **[ADR 074-lum-162](decisions/074-lum-162-conversation-history-ui.md)** (ADR number collides with stack-health ADR — see decisions index).
- **First wow moment:** guided first-query and entity-discovery cards with server-owned readiness and dismissal — see **[ADR 075](decisions/075-lum-216-first-wow-moment.md)**.
- **Admin**: users (import/export, password reset), connector credentials (including household and instance-system tiers where exposed), per-user connector permissions, MCP tokens, audit, diagnostics.
- **Admin stack health (LUM-178):** read-only **System status** panel combining curated admin diagnostics with stack-control service rows (no Docker socket in Core) — see **[ADR 074-lum-178](decisions/074-lum-178-stack-health-dashboard.md)**.
- Password change for self-service and admin-led reset are implemented; email-based forgot-password is not part of the shipped surface.
- Older FastAPI-hosted HTML pages (dashboard, settings, graph views, backup, and similar) still exist for compatibility; full replacement by Lumogis Web is not claimed as finished.

Pin **`LUMOGIS_PUBLIC_ORIGIN`** when authentication is on; align trusted proxy settings if TLS terminates in front of Caddy.

---

## Search & Retrieval

Retrieval combines structured metadata with dense vectors (and optional hybrid / sparse paths) so questions can pull relevant chunks under per-user isolation.

- Qdrant-backed search applies **user_id** filtering on queries.
- Composed prompts sent to a **cloud LLM** (if configured) include retrieval excerpts and bounded context—not the entire local corpus; connectors still reach their own external APIs when used.
- **Auto-RAG (LUM-308, opt-in via `LUMOGIS_AUTO_RAG_ENABLED`):** before each OpenAI-style **`POST /v1/chat/completions`** turn, Core may pull a small slate of relevant **`documents`** chunks (same visibility rules as **`search_files`**), optionally rerank with the configured BGE cross-encoder, wrap them through the injection sanitiser when enabled, and prepend them to the assembled context so the model does not need to call **`search_files`** first. **`search_files`** remains available and dedupes chunks already injected in the same request (matched by Qdrant point id). Operators should expect extra latency (embed + vector search + optional rerank) when auto-RAG is on; the first reranker batch after a cold process start can take seconds while the model loads. **Privacy:** injected **`document:{file_path}`** attribute tokens and chunk text follow the same exposure class as explicit **`search_files`** results—they are sent to whichever LLM you configure for that chat.

---

## Auth / Users / Credentials

Household auth and connector secrets are centered on Core with encrypted storage and scoped visibility.

- Accounts use **`admin`** vs **`user`** roles; bootstrap admin creation applies when the database has no users and bootstrap env is set.
- **`AUTH_ENABLED`** gates interactive auth; JWT access tokens and refresh via httpOnly cookie under **`/api/v1/auth`** implement session rotation.
- Connector credentials are encrypted (including rotation-friendly key handling); saved secrets are not shown again in the UI after save.
- Credential resolution walks **per-user**, **household**, then **instance-system** tiers where configured; decrypt failures fail closed without silent fallback across tiers.
- **Connector permissions** (**Ask** / **Do** / blocked) are **per-user**; APIs exist for users to manage their own modes and for admins to manage others; legacy global permission endpoints are deprecated.
- LLM provider keys, CalDAV, ntfy, and similar integrations use the connector and credential models described in the reference manual.

Treat the deployment as a trusted LAN; wide internet exposure requires extra hardening beyond the default assumptions.

---

## MCP & Tool Catalog

MCP exposes a **curated** subset of Core abilities over streamable HTTP at **`/mcp/`** (trailing slash sensitivity applies to some clients); it is transport for external agents, not the full internal tool registry.

- Per-user opaque MCP tokens can be minted and revoked; when **`AUTH_ENABLED=true`**, MCP gates expect a JWT or an **`lmcp_…`** token as documented; legacy shared **`MCP_AUTH_TOKEN`** behaviour remains only in **`AUTH_ENABLED=false`** mode as described in policy docs.
- Disabled users trigger MCP token revocation in the same transactional flow where the adapter supports it; in-flight JWTs remain valid until their TTL as documented.
- The unified **tool catalog** describes tools and transports (**LLM loop**, **MCP surface**, **catalog-only** observation); **`GET /api/v1/me/tools`** exposes read-model permission labels (**ask** / **do** / **blocked** / **unknown**) without granting rights.
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults **on** when unset; when **off** (explicit **`false`** / **`0`** / **`no`**), the LLM loop does not merge healthy out-of-process capability tools. When **on**, capability tools merge only with valid bearer trust and healthy endpoints, and teardown runs after each request so capability tools do not leak across turns.

---

## Mobile / Offline / Fallback

Lumogis Web targets responsive browsers and companion mobile use; intelligence stays server-side.

- Phase **2** mobile-oriented UX is shipped as an MVP.
- A **PWA** layer includes manifest and service worker work (**precache** and push-related boundaries); this is **not** a full offline product—runtime caching of private **`/api/*`** responses is excluded by policy from the service worker design described for Web Push.
- **Web Push** MVP ships with VAPID **`pywebpush`** flows, explicit permission UX, minimal payloads, and service-worker **`push`** / **`notificationclick`** handling; generic push on every action type is not claimed.
- **LibreChat** remains an optional Compose profile for OpenAI-style chat against **`/v1/*`**—not the household identity surface.

Daily digest and connector-backed notifications remain alternatives where browser push is unavailable or undesirable.

---

## Lumogis Search (desktop overlay)

AGPL-3.0-only Tauri 2 overlay at **`clients/lumogis-search/`** — included in the public export. Connects to your household Lumogis server (no local stack in the installer).

- **Memory search (LUM-329 / LUM-430):** global hotkey frameless UI calling **`GET /api/v1/memory/search`** with OS keychain session storage — see **[ADR 069](decisions/069-lum-329-tauri-search-overlay.md)** and **`clients/lumogis-search/README.md`**.
- **Household onboarding (LUM-398):** in-webview first-run flow (server URL → **`GET /healthz`** → sign-in when auth is on) — see **[ADR 072](decisions/072-lum-398-client-only-overlay.md)**.
- **Overlay auth and ingest paths (LUM-397):** role-gated admin **`ingest_paths`**, push upload, and session refresh — see **[ADR 071](decisions/071-lum-397-tauri-overlay-auth-ingest.md)**.
- **Build:** **`make search-dev`** / **`make search-build`** (or **`cd clients/lumogis-search && npm run tauri:build`**).

---

## Capabilities / Plugins

Extensions split between **in-process plugins** and **out-of-process capabilities**.

- **Plugins** load inside Core under controlled import rules; optional community or example plugins follow the documented layout.
- **Capabilities** are separate HTTP services advertising **`GET …/capabilities`**, **`GET …/health`**, and **`POST …/tools/{name}`** with bearer trust from Core; capability containers must not receive Core Postgres/Qdrant credentials.
- A **mock capability** service exists for contract and CI smoke tests behind an optional Compose overlay—not part of the default stack.

---

## Agentic Delivery

“Agentic” behaviour here means the shipped bounded loop of model completions, optional tool calls, permissions, and audit—not an unconstrained autonomous runtime.

- Chat flows run a **bounded** tool-calling loop with **`run_tool`** execution and connector permission checks (**Ask** vs **Do**).
- **Actions** carry structured audit through append-only logs; tool execution records capability calls where applicable.
- **CapabilityRegistry** discovers optional services and health for bridging tools when catalog integration is enabled.
- **Hooks** and domain **events** synchronously extend Core and plugins at documented extension points.
- **Diagnostics** give admins read-only visibility into flags, stores, capabilities, summaries, and baseline readiness signals—operator diagnosis, not automatic remediation.

---

## Deployment

Pre-built multi-platform images (amd64/arm64) are published to `ghcr.io/lumogis/` on every release. Use `docker-compose.ghcr.yml` for pull-based deployment without building from source:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml docker compose up -d --pull always
```

### Verifying image provenance

After pulling an image, confirm build provenance against the digest you resolved (digest pins are recommended; tags can move):

```bash
gh attestation verify oci://ghcr.io/lumogis/lumogis-orchestrator@sha256:<digest> -R lumogis/lumogis
gh attestation verify oci://ghcr.io/lumogis/lumogis-web@sha256:<digest> -R lumogis/lumogis
```

For convenience you can substitute a tag for the `@sha256:…` suffix (for example `@v0.4.0`); prefer digests when you need a stable verifier subject. Advanced consumers may also inspect in-manifest BuildKit provenance with **`cosign`** or **`docker buildx imagetools`**; this section focuses on **`gh attestation verify`**.

Build-from-source deployment (the default developer flow) remains fully supported via `docker compose up --build`.

---

*Something wrong or missing? [Open a GitHub Issue](https://github.com/lumogis/lumogis/issues).*
