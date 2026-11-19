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
- **Postgres connection pooling:** Core uses an in-process `ThreadedConnectionPool` behind the unchanged `MetadataStore` port (`LUMOGIS_PG_POOL_MIN` / `LUMOGIS_PG_POOL_MAX`, default 2/10) so concurrent routes and scheduler jobs no longer share one connection — see **[ADR 210](decisions/210-lum-816-db-connection-pooling.md)**.
- **Filesystem inbox auto-ingest:** drop supported files into **`ai-workspace/inbox/`** (container path **`/workspace/inbox`**); configurable **`LUMOGIS_INBOX_*`** modes (**`event`**, **`poll`**, **`off`**), write-stability before ingest, poll fallback for unreliable bind mounts, and **`ai-workspace/quarantine/`** for terminal failures — see **[ADR 070](decisions/070-lum-330-folder-watch-inbox.md)** and **`docs/LUMOGIS_REFERENCE_MANUAL.md`**.
- **Ingest job progress:** uploads and batch jobs expose per-stage progress (extract → chunk → embed → graph) via poll endpoints and SSE on **`/events`**; the Library upload panel shows multi-file batch status.
- **Multi-path ingest compose binds:** admin **`PUT /settings`** can list multiple **`ingest_paths`**; indices **1..n** use **`orchestrator/compose_ingest_binds.py`** to generate tiered **`docker-compose.override.yml`** snippets (stack restart still required) — see **[ADR 072-lum-401](decisions/072-lum-401-compose-multibind-generator.md)** (ADR number collides with client-only overlay — see **`docs/decisions/`** index).
- **Signals** ingest external or scheduled inputs (feeds, pages, calendars, email, and similar); monitors poll, score, and persist for downstream use.
- **RSS / web-page outbound policy:** feed and page adapters route every outbound fetch through **`services/outbound_signal_fetch.py`**, reusing the connector URL validator and validating each redirect hop — see **[ADR 216](decisions/216-lum-838-rss-page-outbound-policy.md)**.
- **Source edit and poll health:** **`PATCH /api/v1/sources/{id}`** edits `name`, `category`, and `poll_interval` for every `source_type`; all pollers record `last_poll_error_code`; deleting any source type unschedules its poll job — see **[ADR 215](decisions/215-lum-837-source-edit-and-health.md)**.
- **Per-user source creation:** **`POST /api/v1/sources`** is owner-scoped like sibling routes so household members add their own paperless, RSS, and page sources — see **[ADR 217](decisions/217-lum-839-per-user-source-creation.md)**.
- **Email (IMAP) signal source:** read-only mailbox polling via `feed_monitor` (`source_type="imap"`) with on-box importance/category classification (local model only on mail body); multi-mailbox per user via instance-scoped connector credentials — see **[ADR 174](decisions/174-email-imap-signal-source.md)** and **`docs/LUMOGIS_REFERENCE_MANUAL.md`**.
- **Email (IMAP) egress containment (optional):** IMAP egress is constrained to the declared mailbox host by two honest layers — an in-process allowlist that runs on every deployment (`LUMOGIS_FF_IMAP_EGRESS_GUARD`; in-process, so defense-in-depth, not an external wall) and, on Docker, a Squid `CONNECT`-tunnel proxy (`LUMOGIS_IMAP_EGRESS_PROXY`) that allowlists mail hosts by SNI and refuses undeclared hosts at the network layer — see **[ADR 178](decisions/178-lum-625-email-imap-egress-containment.md)**.
- **Email classification taxonomy:** per-user editable category labels and stored classification rules — see **[ADR 175](decisions/175-email-user-editable-taxonomy.md)**.
- **IMAP label write-back:** optional act-tier sync of Lumogis classification to mailbox folders/flags — see **[ADR 176](decisions/176-email-imap-writeback.md)** and **[ADR 183](decisions/183-email-label-write-back.md)**.
- **IMAP poll cursor safety (ADR 177):** cursor advances only after successful processing so transient failures do not skip messages.
- **Mailbox poll health:** `sources.last_poll_error_code` is stamped at every IMAP connect (not construction) with a four-code taxonomy (`credential`, `egress_blocked`, `network`, `config`) so Email signals surfaces self-healing failure banners — see **[ADR 195](decisions/195-lum-633-poll-health-connect-phase.md)**.
- **OAuth refresh failure kinds:** `OAuthRefreshError` carries the same four-code `kind` at the raise site so token-endpoint timeouts, revoked grants, and missing operator client ids no longer all read as `credential` in the health banner and send path — see **[ADR 196](decisions/196-lum-757-oauth-refresh-failure-kinds.md)**; a transient failure now backs off per mailbox honouring `Retry-After` — see **[ADR 228](decisions/228-lum-761-oauth-refresh-transient-retry.md)**.
- **SMTP send opt-in error mapping:** `PUT /api/v1/me/email-sources/{id}/smtp` maps `load_connection` failures to distinct HTTP status codes (`imap_not_configured`, `imap_reauth_required`, `mailbox_unreachable`, `egress_blocked`, `oauth_unavailable`, and related) instead of a blanket `422 imap_not_configured`; the setup form surfaces a non-blocking notice — see **[ADR 197](decisions/197-lum-759-smtp-optin-error-mapping.md)**.
- **Post-setup send enablement:** mailbox cards expose Sending as a named control after setup (not only during create); OAuth mailboxes enable without a password prompt — see **[ADR 198](decisions/198-lum-769-send-capability-surface.md)**.
- **Plaintext mailbox-host enumeration:** egress allowlists read `(host, port)` from `sources.url` instead of decrypting credential payloads on every enumeration — see **[ADR 199](decisions/199-lum-634-plaintext-mailbox-host-enumeration.md)**.
- **Mailbox IMAP host invariant:** `PUT /me/connector-credentials/imap?instance={source_id}` rejects credential `host`/`port` that do not match the owned `sources.url` (`422 imap_host_mismatch`) — see **[ADR 200](decisions/200-lum-773-mailbox-host-invariant.md)**.
- **Email search:** stores extracted message text at ingest and searches sender, subject, and body locally via **`GET /api/v1/email/search`** with per-field match counts; numeric prefix search; thread quote deduplication — see **[ADR 211](decisions/211-lum-819-email-search.md)** and **`docs/LUMOGIS_REFERENCE_MANUAL.md`** §Email.
- **Process-wide egress ceiling:** boot-baked in-process outbound allowlist complementing per-connector guards — see **[ADR 179](decisions/179-lum-571-process-wide-egress-ceiling.md)**.
- **SMTP send:** household members send mail as themselves via configured SMTP credentials with Ask/Do approvals — see **[ADR 185](decisions/185-smtp-send.md)**.
- **Proactive email surfacing:** snooze re-surface, morning brief, and needs-you push — see **[ADR 180](decisions/180-proactive-email-surfacing.md)**.
- **Routines** automate trusted **Ask** toward **Do** after repeated clean approvals (threshold from configuration).
- Notifications route through an **in-process dispatcher** with per-user **routing preferences** (Postgres-backed), **ntfy**, **Web Push**, and **in-app SSE** channel adapters; daily digest and connector-backed paths remain. Household operators edit the per-type × per-channel matrix in **Lumogis Web → Me → Notifications** — see **[ADR 077](decisions/077-lum-189-notification-architecture.md)** and **[ADR 098](decisions/098-lum-93-notification-settings-ui.md)**.
- Server-sent events are available at **`/events`** for live streams.
- Legacy OpenAI-style **`/v1/*`** routes remain for compatible clients (for example optional LibreChat); Lumogis Web uses the versioned **`/api/v1/*`** façade.

Self-hosters configure the stack via `.env` (see `.env.example`), Compose overlays for optional profiles, and Caddy for TLS and routing.

---

## Knowledge Graph

An optional knowledge graph layer relates extracted entities beyond flat search; basic chat and retrieval do not require it.

- The knowledge graph is **AGPL-3.0-only** and ships in this repository (**[ADR 229](decisions/229-lum-855-kg-agpl-relicence.md)**). It runs in **`service`** mode: the `lumogis-graph` HTTP capability service beside Core, enabled with the **`docker-compose.falkordb.yml`** and **`docker-compose.premium.yml`** overlays. Falkor-backed stacks are activated only when operators add those overlays.
- **`GRAPH_MODE`** selects **`disabled`** (fresh default — no graph bootstrap noise), **`inprocess`**, or **`service`** and must be paired with the matching overlays. **`service` is the only mode that works on the standard Core image.** The image copies `orchestrator/` only, so it never contains `services/lumogis-graph/`, and **`GRAPH_MODE=inprocess` needs a Core build that includes it**. Without it the graph plugin fails to load ("Failed to load plugin: graph") and the graph stays off — chat and ingest continue without KG wiring.
- **Settings → Knowledge graph** (admins only) explains what the graph adds and costs (about 2 GB more memory, about 10 GB in total), says whether it is on and whether this machine has enough memory. On an install from the two Release files it has **Turn on** and **Turn off** buttons (the images are published as `ghcr.io/lumogis/lumogis-graph`, and FalkorDB runs from its upstream `falkordb-server` image; the Release `docker-compose.yml` carries both under a `graph` profile that stays off until switched on); below the memory threshold the admin can still turn it on after confirming. On a git checkout it shows the written steps. Admins also see a dismissible offer line in **System status**. Steps: [quickstart § Turn on the knowledge graph](deployment/quickstart.md#turn-on-the-knowledge-graph).
- In **`service`** mode, Core forwards webhook and context traffic to the graph service; the in-process graph plugin stays inactive so work is not duplicated.
- The **`query_graph`** tool can be bridged to the out-of-process service; **`GRAPH_WEBHOOK_SECRET`** (or an explicit Core opt-in documented for insecure-missing-secret scenarios) is required for that proxy path when **`GRAPH_MODE=service`**—otherwise the bridge stays closed.
- Capability manifests may advertise an operator **`management_url`** for a linked management UI where implemented.

Operators enabling **`service`** mode should coordinate `GRAPH_MODE`, KG base URLs (`KG_SERVICE_URL` / `CAPABILITY_SERVICE_URLS`), shared webhook secrets (`GRAPH_WEBHOOK_SECRET`), and capability discovery using the header of `docker-compose.premium.yml` and [`services/lumogis-graph/README.md`](../services/lumogis-graph/README.md).

---

## Memory & Entities

Structured memory and extracted entities sit alongside search so chat can draw on scoped records, not only raw documents.

- Memory uses **personal**, **shared**, and **system** scopes for isolation and household-wide context where configured.
- Entity extraction stores and links entities through ingestion and session flows.
- Context assembly applies a **context budget** before model calls so prompts stay bounded.
- **What Lumogis knows** — members browse the entries Lumogis has learned (people, organisations, projects, concepts, other) with counts per kind, see which of their documents, chats and AI-tool additions mention an entry, rename an entry or edit its other names, correct what kind it is, merge two entries that are the same, with a History that can undo a merge, and delete an entry so Lumogis does not learn its names again (with a list of those names, an offer to re-read the documents that mentioned it, and **Hide** for entries other members share). Works with the knowledge graph on or off; the map follows a rename or an undo within a day. See [`guides/what-lumogis-knows.md`](guides/what-lumogis-knows.md).

---

## Capture & Voice

Surfaces for quick capture and optional speech input complement chat and ingest.

- **`/capture` (QuickCapture)** uses bounded client-side staging (IndexedDB) with explicit sync steps rather than silent server writes from the queue alone.
- **Capture inbox and archive** — **Inbox** lists pending and failed captures for edit, delete, retry, and commit-to-memory; **Archive** lists indexed captures read-only with provenance (`note_id`, `indexed_at`). Status-filtered **`GET /api/v1/captures`** drives each tab; failed rows include `last_error` for retry UX.
- **Authenticated capture ledger APIs** under **`/api/v1/captures`** create, list, update, and delete capture rows for the signed-in user; support attachments and capture-linked transcription requests; and expose indexing submission so captures can enter the ingest pipeline. Pending captures stay editable; indexed captures block destructive edits enforced by the API.
- **Semantic search** over captures versus ordinary **`documents`** remains uneven—treat capture-oriented discovery as still catching up to library search.
- **Push-to-talk** transcription via **`POST /api/v1/voice/transcribe`** accepts short audio uploads and returns plain text for an editable field. **On by default in an install from the Release files** (a local Speaches engine under the `stt` Compose profile; Lumogis installs the speech model itself) and **off** in a source checkout, where you opt in with the STT Compose overlay (`STT_BACKEND`). Dictation shows "Listening…" with a level meter while you speak and says why when it fails (speech-to-text off, model still downloading, engine not answering). Browsers give a page the microphone only over HTTPS or at `localhost`, so phones and other computers need **Settings → Home address**. A lightweight **`fake_stt`** backend supports tests and scaffolding; an in-process engine is not built.

The engine costs about 3.3 GB of disk and up to about 0.6 GB of memory while it transcribes; see the [quickstart](deployment/quickstart.md#speech-to-text) to turn it off. In a checkout, use the STT Compose overlay and the environment knobs from operator docs.

---

## Lumogis Web

The first-party SPA is the primary household UI: chat, search, approvals, and Me/Admin settings on the same origin as Core via Caddy.

- **Shell** — collapsible primary sidebar (persisted per browser), bottom navigation on small viewports, profile menu in the header, and **Settings → Appearance** for light/dark theme.
- **Me**: profile (including password change), connectors (with **Test connection** for stored credentials — **[ADR 218](decisions/218-lum-834-connector-test-connection.md)**; on/off toggle and per-field set/not-set rows without decrypt — **[ADR 219](decisions/219-lum-834-connector-enabled-and-field-presence.md)**), **What Lumogis may do** (`/me/permissions` — plain-language Ask/Do for send vs tidy-mailbox actions, applies to all mailboxes — **[ADR 220](decisions/220-lum-849-autonomy-surface.md)**), LLM providers, MCP tokens (read-only or read+write scope at mint time), **notification routing preferences** (editable matrix), **My activity** (personal audit log with date presets and event-type filters), export, tools/capabilities overview, **shared items** inventory for household-published documents, entities, and conversations, and **My sources** (`/me/sources`) for paperless, RSS, and page feeds — add, edit, pause/resume, sync and remove (**ADRs 215**, **217** and **226**) with an optional first-run paperless setup offer in the zero state (**[ADR 221](decisions/221-lum-282-paperless-onboarding-offer.md)**).
- **Conversation history:** browse, continue, and delete past conversations with multi-store purge APIs; server-side transcript sync upserts the `web_conversations` header on `PUT` so active-tab messages persist before `POST /session/end` — see **[ADR 074-lum-162](decisions/074-lum-162-conversation-history-ui.md)** and amendment **[ADR 085](decisions/085-lum-439-conversation-put-upsert-fix.md)**.
- **Email triage (`/email`):** unified cross-mailbox lanes with a per-mailbox label on cards when multiple owned mailboxes are connected (**[ADR 213](decisions/213-lum-821-unified-mailboxes.md)**), inline local search (**[ADR 211](decisions/211-lum-819-email-search.md)**), IMAP signal inbox with classification, snooze/done, bulk triage and keyboard shortcuts, reply/reply-all/forward/**compose-new** with a From picker and **`Name — address`** labels resolved on demand (**[ADR 214](decisions/214-lum-831-sending-address.md)**), approval cards, attachment review and filing at **`/email/attachments`** (with per-mailbox activation toggles, rules, filed-from-email list, and per-mailbox paperless overlap consent when creating *Always file* rules — see **[ADR 192](decisions/192-lum-728-paperless-mailbox-overlap.md)** and surface routing **[ADR 193](decisions/193-lum-742-email-surface-routing.md)**), inbox cleanup and organize (with paperless starvation guards — **ADR 191** arc), body read view as extracted text (**[ADR 207](decisions/207-lum-810-email-body-read-view.md)**; text-only IMAP fetch **[ADR 208](decisions/208-lum-814-text-only-imap-fetch.md)**), OAuth Outlook connect, sender unsubscribe, OAuth refresh failure classification (**ADR 196**), SMTP send opt-in error surfacing (**ADR 197**), post-setup send enablement on mailbox cards (**ADR 198**), mailbox-host invariant enforcement (**ADR 200**), and shared-projection Message-ID locate for quote/reply (**[ADR 212](decisions/212-email-shared-projection-message-id-locate.md)**) — see **`docs/LUMOGIS_REFERENCE_MANUAL.md`** §Email and **ADRs 175–219** (connectors **218**–**219**).
- **Recoverable failed sends:** **Approvals → Messages that didn't send** lists terminal failed sends for 30 days with read-only body, explicit sent-log outcome (sent / partially sent / not sent / unknown), attachment availability, and **Save to Drafts** (mailbox recovery, not composer restore) — see **[ADR 202](decisions/202-lum-793-failed-send-recovery.md)** (builds on **[ADR 201](decisions/201-lum-762-action-proposals-claim-not-queue.md)**).
- **Unified document library:** `/documents` lists uploads, watched-folder ingest, paperless, and email-filed documents in one browseable list with origin badges and scoped actions.
- **Voice dictation in composer fields:** reusable push-to-talk button when STT is enabled — see **[ADR 188](decisions/188-voice-dictation.md)**.
- **Household sharing (documents, entities, conversations):** owners publish library documents, search entities, or conversation summaries to shared scope; members discover shared material through Search, Library filters, and shared-items views; graph mode cascades document entities into shared stores when enabled.
- **What Lumogis knows (`/entities`):** reached from Search; entries by kind with counts, a **Mentioned in** section on each entry page (only items you can open), an owner-only **Edit names** dialog, **Delete…** and **Hide from my list**; **Names Lumogis won't learn** at `/me/wont-learn`. No new top-level navigation item.
- **Household invites:** admins mint single-use invite links with optional shared-scope gate for new members; redeem flow sets credentials and optional welcome onboarding.
- **First-run onboarding:** skippable modal when auth is on; **Connect sources** completes onboarding while navigating to **`/me/sources`** (not behind a stuck modal overlay) — see **[ADR 221](decisions/221-lum-282-paperless-onboarding-offer.md)**.
- **First wow moment:** guided first-query and entity-discovery cards with server-owned readiness and dismissal — see **[ADR 075](decisions/075-lum-216-first-wow-moment.md)**.
- **Admin**: users (import/export, password reset, member counts, last-active, promote/demote), connector credentials (including household and instance-system tiers where exposed), per-user connector permissions, MCP tokens (list/revoke only), shared-items oversight, audit, diagnostics, **search and retrieval settings** (BGE reranker toggle with pending-restart honesty), and a read-only **feature flags** table.
- **Admin stack health:** read-only **System status** panel combining curated admin diagnostics with stack-control service rows (no Docker socket in Core) — see **[ADR 074-lum-178](decisions/074-lum-178-stack-health-dashboard.md)**.
- **Admin safety playground** — run a curated injection test suite and ad-hoc probes against live ingest, retrieval, tool-result, secrets, and action-policy defences; dry-run only (no persistence, hooks, or audit writes); disable with **`SAFETY_PLAYGROUND_ENABLED=false`** — see **[ADR 168](decisions/168-lum-141-safety-playground.md)**.
- **Admin Ollama:** discovery, async model pull with job polling, and model delete via typed **`/api/v1/admin/ollama/*`** routes in the same panel — see **[ADR 088](decisions/088-lum-451-ollama-api-v1-promotion.md)**.
- **Admin disaster-recovery backup:** last verified snapshot metadata (age, size, store coverage, stale warning) on **System status** — see **[ADR 098](decisions/098-lum-185-backup-restore.md)** and **`docs/guides/backup-restore.md`**.
- Password change for self-service and admin-led reset are implemented; **email-based forgot-password** ships when an operator enables **Admin → System mail** with a proven send test (**[ADR 209](decisions/209-lum-65-forgot-password-reset.md)**).
- Older FastAPI-hosted HTML pages (dashboard, settings, graph views, backup, and similar) still exist for compatibility; full replacement by Lumogis Web is not claimed as finished.

**`LUMOGIS_PUBLIC_ORIGIN`** is optional: when it is unset Lumogis accepts the address in the browser (still the right setting for a TLS tunnel). Email password reset and other emailed links need it pinned, because Lumogis never builds an email link from the request's `Host`. Align trusted proxy settings if TLS terminates in front of Caddy.

---

## Search & Retrieval

Retrieval combines structured metadata with dense vectors (and optional hybrid / sparse paths) so questions can pull relevant chunks under per-user isolation.

- Qdrant-backed search applies household-union visibility filters (personal plus shared scope where the member is allowed).
- **Document chat:** scoped chat against a single library document with citation metadata — **`POST /api/v1/chat/completions`** with **`document_id`** and Lumogis Web route **`/documents/:documentId/chat`**.
- **Entity search and sharing:** **`GET /api/v1/kg/search`** lists entities for the signed-in user; owners can publish or unpublish entities to shared scope from Search.
- Composed prompts sent to a **cloud LLM** (if configured) include retrieval excerpts and bounded context—not the entire local corpus; connectors still reach their own external APIs when used.
- **BGE reranker (admin UI):** household admins enable or disable the cross-encoder reranker from **Admin → Search and retrieval**; changes may require an orchestrator restart; System status shows live reranker health when active.
- **Auto-RAG (opt-in via `LUMOGIS_AUTO_RAG_ENABLED`):** before each OpenAI-style **`POST /v1/chat/completions`** turn, Core may pull a small slate of relevant **`documents`** chunks (same visibility rules as **`search_files`**), optionally rerank with the configured BGE cross-encoder, wrap them through the injection sanitiser when enabled, and prepend them to the assembled context so the model does not need to call **`search_files`** first. **`search_files`** remains available and dedupes chunks already injected in the same request (matched by Qdrant point id). Operators should expect extra latency (embed + vector search + optional rerank) when auto-RAG is on; the first reranker batch after a cold process start can take seconds while the model loads. **Privacy:** injected **`document:{file_path}`** attribute tokens and chunk text follow the same exposure class as explicit **`search_files`** results—they are sent to whichever LLM you configure for that chat.

---

## Auth / Users / Credentials

Household auth and connector secrets are centered on Core with encrypted storage and scoped visibility.

- Accounts use **`admin`** vs **`user`** roles; bootstrap admin creation applies when the database has no users and bootstrap env is set; admins can invite members and gate **`allows_shared`** per user.
- **`AUTH_ENABLED`** gates interactive auth; JWT access tokens and refresh via httpOnly cookie under **`/api/v1/auth`** implement session rotation.
- **Cloud LLM privacy mode** defaults to local-only on fresh installs; operators and members can tighten or relax policy within admin bounds; blocked remote calls are audit-logged and chat falls back to local Ollama when configured.
- Connector credentials are encrypted (including rotation-friendly key handling); saved secrets are not shown again in the UI after save.
- Credential resolution walks **per-user**, **household**, then **instance-system** tiers where configured; decrypt failures fail closed without silent fallback across tiers.
- **Connector permissions** (**Ask** / **Do** / blocked) are **per-user**; the **What Lumogis may do** page (`/me/permissions`) is the primary household-facing surface for email send vs tidy-mailbox Ask/Do (**[ADR 220](decisions/220-lum-849-autonomy-surface.md)**); APIs also exist for admins to manage others; optional **scope arrays** grant fine-grained capability permissions declared on manifests; legacy global permission endpoints are deprecated.
- LLM provider keys, CalDAV, IMAP mailboxes, ntfy, and similar integrations use the connector and credential models described in the reference manual.
- **Password recovery** is phased. The login screen always states how to recover: ask a household admin, or use the operator CLI. An admin can additionally enable **email reset** at **Admin → System mail**, which requires an operator-configured transactional mailer that has **passed a live Send test** — configuration alone is never taken as proof of delivery. When enabled, the public request path is protected by a self-hosted **ALTCHA** proof of work (no cloud CAPTCHA — a household box should not phone home to verify its own users) and per-IP/per-address rate limits, and answers identically whether or not the address has an account. Reset mail is queued durably and sent by a background job, so it arrives seconds after the request. SMS reset is out of scope.

Treat the deployment as a trusted LAN; wide internet exposure requires extra hardening beyond the default assumptions.

---

## MCP & Tool Catalog

MCP exposes a **curated** subset of Core abilities over streamable HTTP at **`/mcp/`** (trailing slash sensitivity applies to some clients); it is transport for external agents, not the full internal tool registry.

- Per-user opaque MCP tokens can be minted and revoked with optional **`mcp:read`** / **`mcp:write`** scopes; omitted scopes mint read-only tokens; when **`AUTH_ENABLED=true`**, MCP gates expect a JWT or an **`lmcp_…`** token as documented; legacy shared **`MCP_AUTH_TOKEN`** behaviour remains only in **`AUTH_ENABLED=false`** mode as described in policy docs.
- **Read tools** include memory search, entity lookup, context building, and **`recall`** (hybrid semantic + keyword + graph + temporal fusion).
- **Write tools** (**`add_memory`**, **`add_entity`**, **`add_relation`**, **`forget`**, **`update_observation`**, **`checkpoint`**) require a token carrying **`mcp:write`**; memories land in bank-scoped Postgres and Qdrant with optional graph projection.
- **Cursor stdio bridge** — AGPL package **`clients/lumogis-mcp/`** forwards MCP over stdio to Core **`/mcp/`**; install with **`make lumogis-cursor-install`**.
- Disabled users trigger MCP token revocation in the same transactional flow where the adapter supports it; in-flight JWTs remain valid until their TTL as documented.
- The unified **tool catalog** describes tools and transports (**LLM loop**, **MCP surface**, **catalog-only** observation); **`GET /api/v1/me/tools`** exposes read-model permission labels (**ask** / **do** / **blocked** / **unknown**) without granting rights.
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults **on** when unset; when **off** (explicit **`false`** / **`0`** / **`no`**), the LLM loop does not merge healthy out-of-process capability tools. When **on**, capability tools merge only with valid bearer trust and healthy endpoints, and teardown runs after each request so capability tools do not leak across turns.

---

## Mobile / Offline / Fallback

Lumogis Web targets responsive browsers and companion mobile use; intelligence stays server-side.

- Phase **2** mobile-oriented UX is shipped as an MVP.
- A **PWA** layer includes manifest and service worker work (**precache** and push-related boundaries); this is **not** a full offline product—runtime caching of private **`/api/*`** responses is excluded by policy from the service worker design described for Web Push.
- **Web Push** MVP ships with VAPID **`pywebpush`** flows, explicit permission UX, minimal payloads, and service-worker **`push`** / **`notificationclick`** handling; generic push on every action type is not claimed.
- **Secure address at home** (Settings → Home address): a free `https://….homes.lumogis.app` name with a real certificate, for phones and laptops on the home network, so app install, voice input and push work there with nothing installed on the device. Home network only; turning it on publishes the name and this computer's private address in public DNS. See [remote access § Secure address at home](deployment/remote-access.md#secure-address-at-home).
- **LibreChat** remains an optional Compose profile for OpenAI-style chat against **`/v1/*`**—not the household identity surface.

Daily digest and connector-backed notifications remain alternatives where browser push is unavailable or undesirable.

---

## Lumogis Search (desktop overlay)

AGPL-3.0-only Tauri 2 overlay at **`clients/lumogis-search/`** — included in the public export. Connects to your household Lumogis server (no local stack in the installer).

**Personas A and B** share the same client-only Search installer (`lumogis-overlay-*`); only the configured server URL differs — localhost for **Persona A** (Docker Compose on the same machine) vs a household URL for **Persona B**. See the [Persona A / B / C distribution matrix](LUMOGIS_REFERENCE_MANUAL.md#persona-a--b--c--distribution-matrix) and [Persona A install steps](../clients/lumogis-search/README.md#persona-a--docker-track-localhost).

- **Memory search:** global hotkey frameless UI calling **`GET /api/v1/memory/search`** with OS keychain session storage — see **[ADR 069](decisions/069-lum-329-tauri-search-overlay.md)** and **`clients/lumogis-search/README.md`**.
- **System tray:** menu with **Show Lumogis** and **Quit** for summon/recovery when the overlay is hidden (alongside the global hotkey) — see **[ADR 090](decisions/090-lum-457-system-tray-all-personas.md)**.
- **Settings panel:** persona-aware layout (client-only server URL, library roots, admin ingest paths, push upload, restart-required banner) — see **[ADR 091](decisions/091-lum-463-persona-aware-search-settings.md)** and **[ADR 071](decisions/071-lum-397-tauri-overlay-auth-ingest.md)**.
- **Build:** **`make search-dev`** / **`make search-build`** (or **`cd clients/lumogis-search && npm run tauri:build`**).
- **Public release CI:** **`.github/workflows/search-overlay-build.yml`** exports to the AGPL tree; **`search-v*`** tags on **`lumogis/lumogis`** produce installer artefacts — see **[ADR 082](decisions/082-lum-433-search-overlay-public-ci.md)**.
- **Export boundary:** **Lumogis Search** is the desktop client in the public AGPL tree — see **[ADR 081](decisions/081-lum-434-export-boundary-reconciliation.md)** and **[ADR 100](decisions/100-lum-491-fused-hub-cleanup.md)**.

---

## Capabilities / Plugins

Extensions split between **in-process plugins** and **out-of-process capabilities**.

- **Plugins** load inside Core under controlled import rules; only first-party modules are permitted — community code must run out-of-process.
- **Capabilities** are separate HTTP services advertising **`GET …/capabilities`**, **`GET …/health`**, and manifest-declared **`POST …` invoke paths** with bearer trust from Core; capability containers must not receive Core Postgres/Qdrant credentials.
- **Capability invoke contract v1** — manifests declare per-tool invoke paths, a versioned request/response envelope, structured errors, optional auth modes, and optional **`permissions_required`** scopes; see **`docs/extending/capability-contract-v1.md`** and **[ADR 169](decisions/169-lum-41-capability-invoke-contract-v1.md)**.
- **First-party allowlist** — operator-maintained **`orchestrator/first_party_capabilities.txt`** pins trusted capability ids to expected discovery origins; anything else is **community (untrusted)** and is refused from the LLM catalog by default.
- **Community egress containment (optional)** — Docker Compose overlay places community containers on an isolated network and routes outbound traffic through a Squid proxy that allowlists only declared **`external_endpoints`**; Core dispatches community capabilities only when a containment marker lists them — see **`docs/guides/capability-egress-containment.md`** and **[ADR 173](decisions/173-lum-618-container-egress-enforcement.md)**.
- A **mock capability** service exists for contract and CI smoke tests behind an optional Compose overlay—not part of the default stack.

---

## Agentic Delivery

“Agentic” behaviour here means the shipped bounded loop of model completions, optional tool calls, permissions, and audit—not an unconstrained autonomous runtime.

- Chat flows run a **bounded** tool-calling loop with **`run_tool`** execution and connector permission checks (**Ask** vs **Do**) plus optional **capability scope** grants.
- **Action proposal execution:** approved proposals use atomic **`claim_by_id`** over synchronous execution — failure is terminal on the first attempt (no background drain or retry ladder) — see **[ADR 201](decisions/201-lum-762-action-proposals-claim-not-queue.md)** (amends **[ADR 040](decisions/040-action-proposals-atomic-claim.md)**).
- **Failed-send recovery:** terminal failed email sends remain listed on Approvals with **`list_recoverable_sends`**; recovery writes a draft to the user's mailbox via **`email_save_draft`** rather than re-opening the composer — see **[ADR 202](decisions/202-lum-793-failed-send-recovery.md)**.
- **Injection defences** — ingest sanitisation, optional secrets scanning on user-authored config, and optional tool-result scanning before context injection; admins can validate live rules from the safety playground.
- **Actions** carry structured audit through append-only logs; tool execution records capability calls where applicable.
- **CapabilityRegistry** discovers optional services and health for bridging tools when catalog integration is enabled.
- **Hooks** and domain **events** synchronously extend Core and plugins at documented extension points.
- **Diagnostics** give admins read-only visibility into flags, stores, capabilities, summaries, and baseline readiness signals—operator diagnosis, not automatic remediation.

---

## Deployment

Lumogis installs from published images. Each GitHub Release carries a `docker-compose.yml` and an `example.env`; in an empty folder, save them (the second as `.env`) and run `docker compose up -d`. Nothing is built and no source checkout is needed. Update with `docker compose pull && docker compose up -d` (**[Upgrading](deployment/upgrading.md)**).

The five Lumogis images (`lumogis-orchestrator`, `lumogis-web`, `lumogis-stack-control`, `lumogis-backup`, `lumogis-caddy`) are multi-platform (amd64/arm64) on `ghcr.io/lumogis/`, tagged `X.Y.Z`, `X.Y` and `release` (the latest release, the default `LUMOGIS_VERSION`), plus `latest` for the head of public `main`. A release is published only after its files and images have been pulled anonymously by digest; its `image-digests.txt` lists those digests. In a git checkout, `docker-compose.ghcr.yml` pulls the same images instead of building:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml docker compose up -d
```

### Verifying image provenance

After pulling an image, confirm build provenance against the digest you resolved (digest pins are recommended; tags can move):

```bash
gh attestation verify oci://ghcr.io/lumogis/lumogis-orchestrator@sha256:<digest> -R lumogis/lumogis
gh attestation verify oci://ghcr.io/lumogis/lumogis-web@sha256:<digest> -R lumogis/lumogis
```

The same works for `lumogis-stack-control`, `lumogis-backup` and `lumogis-caddy`. The release's `docker-compose.yml` and `example.env` are attested too: `gh attestation verify docker-compose.yml -R lumogis/lumogis`.

For convenience you can substitute a tag for the `@sha256:…` suffix (for example `@v0.4.0`); prefer digests when you need a stable verifier subject. Advanced consumers may also inspect in-manifest BuildKit provenance with **`cosign`** or **`docker buildx imagetools`**; this section focuses on **`gh attestation verify`**.

Build-from-source deployment (the developer flow) remains fully supported: in a git checkout, `docker compose up -d` builds the Lumogis images.

### Disaster-recovery backups

Instance-scoped **automated snapshots** of Postgres, Qdrant, and (when graph is enabled) FalkorDB run via a optional **`backup`** Compose service and shared **`scripts/backup/`** toolkit:

| Command | Purpose |
| --- | --- |
| `make backup` | Run one DR snapshot now |
| `make backup-verify` | Re-verify the latest snapshot |
| `make backup-prune` | Apply retention (7 daily + 4 weekly) |
| `make restore SNAPSHOT=YYYYMMDD-HHMMSS` | Restore a snapshot (requires `--yes`) |

Default host path: **`./ai-workspace/backups`** (`BACKUP_HOST_DIR`). See **`docs/guides/backup-restore.md`**. The legacy **`POST /backup`** JSON zip remains a lightweight logical export, not the canonical DR path.

### Contributor onboarding

The public export includes **`CONTRIBUTING-BEGINNERS.md`** at the repository root — a gentler entry point than `CONTRIBUTING.md`, with prerequisites, clone/build smoke steps, and a fenced copy-paste prompt for AI-assisted first contributions. Maintainer templates live under `docs/public-export/` and are copied into the AGPL tree by `scripts/create-upstream-export-tree.sh`.

---

*Something wrong or missing? [Open a GitHub Issue](https://github.com/lumogis/lumogis/issues).*
