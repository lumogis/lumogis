# Lumogis Product Portfolio

**Last updated:** 2026-05-07
**Linear team:** LUM (lumogis.linear.app)
**Maintained by:** `/verify-plan` and maintainer quarterly review
**Not a backlog** — active work lives in Linear. This file is navigation and confidence reference.

---

## How to read this file

| Field | Meaning |
|-------|---------|
| **Shipped** | Implemented, verified, has ADR or plan Implementation Log |
| **MVP closed** | Shipped for the current product tier (family-LAN / self-hosted); full scope may have follow-ups |
| **Planned** | Has a Linear issue and/or active plan; not yet implemented |
| **Deferred** | Explicitly out of scope for current tier; revisit conditions documented |
| **Rejected** | Evaluated and not pursued; rationale recorded |
| **AGPL posture** | `core` = in public AGPL tree; `app` = private product tree only; `mixed` = partly each |
| **Confidence** | Maintainer confidence in current state accuracy (High / Med / Low) — review quarterly |

---

## 1. Auth / Users / Credentials

**Linear project:** Auth / Users / Credentials
**AGPL posture:** core (auth substrate) + app (credential encryption, tiers)
**Confidence:** High

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| JWT auth (`AUTH_ENABLED=true`), users table, bootstrap admin | ✅ Shipped | ADR 010, ADR 012 |
| Per-user connector credentials (Fernet-encrypted, tiers) | ✅ Shipped | ADR 018, ADR 027 |
| Household + system credential tiers | ✅ Shipped | ADR 027 |
| Credential management UX (dashboard tile, register, JSON hints) | ✅ Shipped | ADR 020 |
| Per-user connector permissions (Ask/Do per user) | ✅ Shipped | ADR 024 |
| CalDAV connector credentials | ✅ Shipped | ADR 021 |
| MCP token → user map | ✅ Shipped | ADR 017 |
| Per-user LLM provider keys | ✅ Shipped | ADR 026 |
| Self-hosted forgot-password / email reset flow | 🗂 Planned | LUM-65 |
| Per-user backup: CSRF un-skip, erasure, 410/507 | 🗂 Planned | LUM-35 |
| JWT access-token revocation, multi-device sessions | 🗂 Planned | LUM-29 |
| Double-submit CSRF (optional beyond SameSite+Origin) | 🗂 Planned | LUM-31 |
| Household key health; admin enumeration | 🗂 Planned | LUM-33 |
| Credential scopes: operator smoke, audit docs, route split | 🗂 Planned | LUM-32 |
| Credential mgmt UX: smokes, register(), JSON hints | 🗂 Planned | LUM-46 |
| `include_credentials` revisit (cross-instance) | 🗂 Planned | LUM-50 |
| resolve_runtime_credential: CalDAV, ntfy, LLM | 🗂 Planned | LUM-24 |
| LLM per-user: dashboard, e2e, cache/perf | 🗂 Planned | LUM-22 |
| Review queue: GET admin-only (inbox TBD) | 🗂 Planned | LUM-59 |
| Connector permissions: tile, cache, scopes, multi-worker | 🗂 Planned | LUM-34 |
| Per-user Qdrant collections (physical isolation) | ⏸ Deferred | Phase C / hosted multi-tenant only — ADR 012 D10 |
| Hosted multi-tenant OIDC / session management | ⏸ Deferred | Phase C — out of scope for family-LAN tier |

---

## 2. Core Platform

**Linear project:** Core Platform
**AGPL posture:** core
**Confidence:** High

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| FastAPI orchestrator, ports-and-adapters architecture | ✅ Shipped | ADR 001–008 |
| Structured logging + audit (structlog, correlation IDs) | ✅ Shipped | ADR 019 |
| Batch jobs: admin BT→queue, projection, dead letter, sidecar | 🗂 Planned | LUM-25 |
| Rate limiter → shared store (multi-uvicorn) | 🗂 Planned | LUM-30 |
| Structlog pkg share; JSONB audit; request_id 401 | 🗂 Planned | LUM-38 |
| ntfy: 410 body; multi-worker fanout | 🗂 Planned | LUM-39 |
| Multi-worker shared rate limit store | 🗂 Planned | LUM-30 |

---

## 3. Lumogis Web (Cross-device)

**Linear project:** Lumogis Web
**AGPL posture:** core (API façade) + app (proprietary connectors, premium UI)
**Confidence:** Med — phases 3–6 still open

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Phase 1: same-origin profile, default `LUMOGIS_PUBLIC_ORIGIN`, Caddy security headers | ✅ Shipped | ADR 030, cross_device plan Phase 1 |
| Phase 2: mobile UX, responsive shell, PWA basics | ✅ Shipped | ADR 030, cross_device plan Phase 2 |
| Phase 3: `/api/v1/*` façade, auth-aware web client | 🗂 Planned | LUM-44 |
| Phase 4: Web Push (`ACTION_EXECUTED` templates) | 🗂 Planned | LUM-28 |
| Phase 5: capture from web (photos, voice, files) | 🗂 Planned | LUM-44 + children LUM-89–92 |
| Phase 6: Tauri desktop shell | ⏸ Deferred | LUM-44 — post-PWA validation |
| Cross-device: open questions, UX/security | 🗂 Planned | LUM-27 |
| Notification settings write path | 🗂 Planned | LUM-93 |
| Notifier prefs UX tile + `GET /api/v1/me/notifier` | 🗂 Planned | LUM-97 |
| Out-of-scope (multi-user cross-device not in v1) | 📋 Tracked | LUM-68 |
| Optional CI web e2e (`make web-e2e`) | 🗂 Planned | LUM-60 |

---

## 4. Knowledge Graph

**Linear project:** Knowledge Graph
**AGPL posture:** mixed — graph substrate in core; graph service (`lumogis-graph`) proprietary
**Confidence:** Med

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| FalkorDB graph service, entity extraction, `/graph/mgm` | ✅ Shipped | ADR 009, 011 |
| Entity deduplication (Splink, review queue, auto-merge) | ✅ Shipped | ADR 011 |
| Memory scopes (`personal` / `shared` / `system`) | ✅ Shipped | ADR 015 |
| Remove in-core plugin; `graph_projection_state` | 🗂 Planned | LUM-40 |
| KG: drift detection when real data (§8) | 🗂 Planned | LUM-57 |
| KG: NER/spaCy, REBEL (Phase 5+) | 🗂 Planned | LUM-58 |
| Test debt: graph backfill / reconcile | 🗂 Planned | LUM-54 |
| DEBT: `/graph/stats` Cypher `user_id=default` | 🗂 Planned | LUM-23 |
| KG quality pipeline execution | 🗂 Planned | LUM-57 (parent) |
| Per-user FalkorDB graphs (physical) | ⏸ Deferred | Phase C — ADR 012 D10 |

---

## 5. Capture & Voice

**Linear project:** Capture & Voice
**AGPL posture:** core (capture pipeline) + app (voice connectors)
**Confidence:** Med

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Voice capture (openai-whisper), audio memo ingest | ✅ MVP closed | ADR 031 |
| STT-2D: in-process `faster_whisper` adapter | 🗂 Planned | LUM-45 |
| Capture semantic search parity (FP-TBD-5.1) | 🗂 Planned | LUM-89 |
| Default indexing consent and policy UX (FP-TBD-5.2) | 🗂 Planned | LUM-90 |
| Purge indexed capture from memory (FP-TBD-5.5) | 🗂 Planned | LUM-91 |
| EXIF hard-strip on photo upload (FP-TBD-5.12) | 🗂 Planned | LUM-92 |
| Conversational voice PWA/native, TTS | ⏸ Deferred | LUM-67 — post-STT-2D |
| Wake word / companion programme (FP-TBD-5.13) | ⏸ Deferred | Post-launch |
| Cloud STT opt-in product (FP-TBD-5.14) | ⏸ Deferred | Post-launch |

---

## 6. Search / Retrieval

**Linear project:** Search / Retrieval
**AGPL posture:** core
**Confidence:** High

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Semantic search (Qdrant), fuzzy filename search | ✅ Shipped | ADR 013 |
| Per-user Qdrant payload filtering | ✅ Shipped | ADR 012 + 015 |
| web_search: SearXNG, hook, per-user settings | 🗂 Planned | LUM-56 (+ children LUM-48, 66, 79) |
| Deep research: sandbox, STORM, caps | ⏸ Deferred | LUM-48 — post web_search |
| News aggregation: APIs, crawler, readers | ⏸ Deferred | LUM-66 |
| Home automation integration | ⏸ Deferred | LUM-79 — Phase 7a |

---

## 7. MCP & Tool Catalog

**Linear project:** MCP & Tool Catalog
**AGPL posture:** core (MCP protocol, tool catalog) + app (proprietary connectors)
**Confidence:** Med

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| MCP server, unified tool catalog, `ToolExecutor` | ✅ Shipped | ADR 017, ADR 028 |
| Per-user MCP token auth | ✅ Shipped | ADR 017 |
| MCP: KG gateway, expiry, scopes, admin/forensic | 🗂 Planned | LUM-26 |
| Stateful MCP in separate capability | ❌ Rejected | LUM-63 (Canceled) |

---

## 8. Capabilities / Plugins

**Linear project:** Capabilities / Plugins
**AGPL posture:** mixed — plugin boundary in core (ADR 005); capability services proprietary
**Confidence:** Med

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Capability scaffold, HTTP execution bridge, health/registry | ✅ Shipped | ADR 028 |
| Capability invoke contract v1 (manifest-declared URL) | 🗂 Planned | LUM-41 |
| KG capability auth hardening (optional-bearer legacy) | 🗂 Planned | LUM-42 |
| Capability compose/policy guard (no Core DB/Qdrant creds) | 🗂 Planned | LUM-43 |
| Richer `/api/v1/me/tools` unavailable reasons | 🗂 Planned | LUM-61 |
| Test debt: `capability_health` vs lifespan | 🗂 Planned | LUM-53 |
| Capability launchers & gateway programme | 🗂 Planned | LUM-78 (umbrella) |
| Marketplace-grade third-party plugins | ⏸ Deferred | Hosted multi-tenant only — ADR 028 |

---

## 9. Memory & Entities

**Linear project:** Memory & Entities
**AGPL posture:** core
**Confidence:** High

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| File index, ingest attribution (per-user `point_id`) | ✅ Shipped | ADR 013 |
| Personal / shared / system memory scopes | ✅ Shipped | ADR 015 |
| Entity extraction, relations, evidence dedup | ✅ Shipped | ADR 011 |
| File index: test-debt, backfill, sessions, audio_memos | 🗂 Planned | LUM-47 |
| Memory scopes: compose tests, ADR revisit list | 🗂 Planned | LUM-36 |
| `entity_relations` sibling / timeline (revisit) | 🗂 Planned | LUM-52 |

---

## 10. Mobile / Offline / Fallback

**Linear project:** Mobile / Offline / Fallback
**AGPL posture:** core (sync protocol) + app (native connectors)
**Confidence:** Low — draft plan only, not yet explored

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Mobile / offline / cloud fallback sync | 🗂 Planned | LUM-77 (draft plan triage) |
| Offline photo IDB staging (FP-TBD-5.10b) | 🗂 Planned | LUM-77 child |
| PWA via Tailscale Serve | 🗂 Planned | LUM-44 prerequisite |

---

## 11. Release / Export Hygiene

**Linear project:** Release / Export Hygiene
**AGPL posture:** app (release tooling) — public export workflow documented
**Confidence:** High

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| AGPL-3.0-only licence metadata, SPDX standardisation | ✅ Shipped | ADR 032 |
| Public/private release workflow (`dev → main → public`) | ✅ Shipped | `docs/release/` |
| Licence docs: sync plan files + ADR narrative to SPDX | 🗂 Planned | LUM-55 |
| `include_credentials` revisit (cross-instance export) | 🗂 Planned | LUM-50 |
| per_user_backup: CSRF un-skip, erasure, 410/507 | 🗂 Planned | LUM-35 |
| Recurring drift-prevention reconciliation | 🗂 Planned | LUM-87 (blocked by LUM-88) |
| Linear evidence index | 🗂 Planned | LUM-86 |

---

## 12. Docs & Governance

**Linear project:** Docs & Governance
**AGPL posture:** mixed — architecture docs in core; private audit docs app-only
**Confidence:** High (post-session 2026-05-07)

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Linear Product OS migration (LUM-5 programme) | ✅ Done | LUM-5 (Done) |
| Agentic workflow foundation (LUM-82 programme) | ⚠️ Partial | LUM-82 — streams 1–3 done; LUM-86, 87 open |
| Product portfolio doc (this file) | ✅ Done | LUM-7 |
| Private audit: FILESYSTEM, Splink, S2S (documented deferrals) | ✅ Done | LUM-64 (Done — intentional deferrals) |
| chatGPT ecosystem reference model (commercial boundary) | ✅ Done | LUM-80 (Done — moved to devtools) |
| LibreChat deprecation decision | 🗂 Planned | LUM-95 |
| OpenAPI codegen check without live orchestrator | 🗂 Planned | LUM-94 |
| Local license validation design (premium) | 🗂 Planned | LUM-96 |
| LUMOGIS_PRODUCT_PORTFOLIO.md creation | ✅ Done | LUM-7 |

---

## 13. Agentic Delivery System

**Linear project:** Agentic Delivery System
**AGPL posture:** core (Cursor skills, navigator) + app (agentic connectors)
**Confidence:** Med

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Navigator skill (priority scoring, `/navigator sync`) | ✅ Shipped | LUM-83 (Done) |
| Agentic Linear ↔ Cursor workflow design | ✅ Shipped | LUM-85 (Done) |
| Linear evidence index | 🗂 Planned | LUM-86 |
| Drift prevention reconciliation | 🗂 Planned | LUM-87 |
| Fixture tests for exploration linkage checker | 🗂 Planned | LUM-88 |
| Agentic Core programme (ambient voice, proactive AI) | ⏸ Deferred | LUM-76 — post voice/capture completion |

---

## 14. Public AGPL Release

**Linear project:** Public AGPL Release
**AGPL posture:** app (release tooling and scripts)
**Confidence:** High

| Area | Status | Evidence / Linear |
|------|--------|-------------------|
| Public open core repo (`lumogis/lumogis`) | ✅ Shipped | `docs/release/public-agpl-release-workflow.md` |
| Dual-repo workflow documented | ✅ Shipped | `docs/private/open-core-repository-workflow.md` |
| Lumogis Security Audit v0.3.0-rc | ✅ Shipped | `docs/SECURITY-AUDIT-001.md` |
| AGPL narrative sync (plans + ADR examples to SPDX) | 🗂 Planned | LUM-55 |

---

## Appendix A — Linear project → LUM umbrella map

| Linear project | Primary umbrella issue |
|----------------|----------------------|
| Auth / Users / Credentials | LUM-51 |
| Core Platform | LUM-25 |
| Lumogis Web | LUM-44 |
| Knowledge Graph | LUM-40 |
| Capture & Voice | LUM-45 |
| Search / Retrieval | LUM-56 |
| MCP & Tool Catalog | LUM-26 |
| Capabilities / Plugins | LUM-78 |
| Memory & Entities | (standalone issues) |
| Mobile / Offline / Fallback | LUM-77 |
| Release / Export Hygiene | LUM-21 |
| Docs & Governance | LUM-82 |
| Agentic Delivery System | LUM-76 |
| Public AGPL Release | LUM-21 |

---

## Appendix B — Product tiers and AGPL boundary

| Tier | Description | Repo |
|------|-------------|------|
| **Free / BYOK** | Full orchestrator, local LLMs, all core features — AGPL-3.0-only | `lumogis/lumogis` (public) |
| **Pro (~€12/mo)** | Cloud LLM routing, premium capability services, priority support | `lumogis/lumogis-app` (private) |
| **Teams** | Planned — multi-household, admin console | Future |

The AGPL boundary is architectural: open-core engine (`lumogis-core`) + proprietary graph layer, connectors, desktop app, and cloud services. See `docs/private/open-core-repository-workflow.md` and LUM-96 (license validation design).

---

*Update cadence: refresh after each significant ADR finalisation or Linear programme closure. Maintainer reviews quarterly.*
