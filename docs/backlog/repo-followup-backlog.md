# Repository Follow-up Backlog

> **Live register (do not maintain this file for current priorities by hand).** The skill-managed, **priority-sorted** follow-up portfolio is ***(maintainer-local only; not part of the tracked repository)***, updated by **`/verify-plan`** (and **`/record-retro`** for revisit items). **2026-04-22:** That file was **seeded** from this document (BL-001—BL-045 → FP-001—FP-045, same order/titles, **L**-sorted in the table). This sweep file remains for **narrative detail and evidence**; the portfolio row is the execution queue.

## Purpose

A consolidated backlog of deferred, out-of-scope, open-question, and recommended-later items extracted from ADRs, plans, explorations, reviews, and Cursor task files across the repository.

## How this file was built

- **Folders scanned:** Recursively under *(maintainer-local only; not part of the tracked repository)* (including `adrs/`, `explorations/`, `plans/`, `reviews/`, `skills/`, `rules/`, `topics.md`, `README.md`) and `docs/` (including `docs/decisions/`, `docs/private/`, and top-level docs). Additional spot-checks on repository-root `*.md` (e.g. `ARCHITECTURE.md`, `REMEDIATION-PLAN.md`, `README.md`) where they might plausibly contain planning follow-ups. `docs/backlog/`, `docs/decisions/`, and *(maintainer-local only; not part of the tracked repository)* index paths were treated as skill-owned only where the workflow forbids ad-hoc edits; this *backlog* file is a normal `docs/` artefact, not a skill finalised ADR.
- **Patterns used:** `deferred`, `follow-up`, `next step`, `open question`, `not in scope` / `out of scope`, `future work`, `revisit` / `revisit conditions` / `revisit when`, `sibling` / `separate` / `later chunk`, `phase 2/3` / `later phase`, `post-MVP` / `post-launch`, `backlog`, `intentionally excluded`, `optional later`, `named follow-up`, `NOT in this plan`, and related phrases, applied via `grep(1)` over Markdown sources (workspace search skipped some very large plan files; the same content was double-checked with shell `grep` where needed).
- **Merging and deduplication:** Items that refer to the same work under different filenames (e.g. finalised `docs/decisions/NN-*.md` vs draft *(maintainer-local only; not part of the tracked repository)* vs plan tails) were **merged into a single normalised backlog line**; **all contributing paths are listed under **Sources** for traceability.
- **Grounding rule:** Each item below is **anchored in at least one file** in the repo. No net-new product requirements were invented here.

## Summary

| Metric | Count |
|--------|------:|
| Markdown / text files under ***(maintainer-local only; not part of the tracked repository)* and `docs/`** used as the primary corpus | **167** |
| Additional root-level `*.md` files spot-checked for follow-up phrasing | **8** (no extra backlog entries found without overlap) |
| **Backlog item entries (BL-###) in the main list** | **45** (BL-001 — BL-045) |
| **Unique work streams after merging duplicates** (same as BL count) | **45** |
| **Items in “Needs confirmation” (ambiguous or weakly phrased)** | **6** |
| **Excluded categories** (see end) | n/a (qualitative) |

> **How to use with `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md`:** That file is a **stakeholder-oriented master table** of multi-user audit follow-ups. This backlog is **wider** (debt, roadmap drafts, product explorations) but **reuses the same source-of-truth paths**; where they overlap, treat both as cross-references to the same underlying work, not two independent truths.

## Backlog Items

### BL-001
**Title:** Ship Lumogis Web, `/api/v1/*` façade, and phased cross-device plan  
**Status:** Recommended next step  
**Theme:** UI / client; deployment; A1 (audit)  
**Priority:** High  
**Why it exists:** The multi-user audit’s **A1** path is **Lumogis Web** (not a LibreChat identity bridge). The first-party client and reverse-proxy/Caddy work remain the primary delivery vehicle for same-origin auth, mobile/PWA, and follow-on work that unblocks cookie-era tests elsewhere.  
**Recommended follow-up:** Execute *(maintainer-local only; not part of the tracked repository)* (Phases 0–n) and resolve its **Open questions** / self-review / arbitration left-overs.  
**Original scope context:** Replaces LibreChat bridge work (see ADR 012, family plan); multi-user **backend** already exists — **client and façade** are the gap.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-RESPONSE.md` — A1 “replaced” by first-party web  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Section 4A  
- *(maintainer-local only; not part of the tracked repository)* — “What this builds”  
- `docs/decisions/012-family-lan-multi-user.md` — Lumogis Web as first-party surface  

**Evidence snippets:**  
> A first-party browser client (**Lumogis Web**) and a stable, versioned **client API façade** (`/api/v1/*`) on the existing FastAPI orchestrator…  
> **Implement** Phases 0–n of cross_device_lumogis_web.plan.md…

---

### BL-002
**Title:** Cross-device plan — open questions, UX/security tail, and known uncertainties  
**Status:** Open question  
**Theme:** UI; auth; Web Push; ops  
**Priority:** High  
**Why it exists:** The plan records unresolved **Q1–Q16**-style items (e.g. browser↔KG latency, refresh revocation, WCAG, SW caching, Tauri, Mongo after LibreChat, `web_conversations` persistence, Web Push cap, password verify, auth table scaffolding) plus arbitration/self-review drift notes.  
**Recommended follow-up:** Triage the **Open questions** and **Arbitration / self-review** sections in the plan in issue-sized chunks, closing or spinning ADRs as needed.  
**Original scope context:** Same plan as BL-001; these are the **unanswered knobs** and **file follow-ups** called out in-plan.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — `## Open questions` and arbitration tail  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Section 4A “Resolve Open questions” and “Self-Review / Arbitration leftovers”  

**Evidence snippets:**  
> **Out of scope:** local KG mirror, offline tool execution, native iOS/Android packaging…  
> 12. **Server-side conversation persistence:** … Out of scope for v1 — recorded so the Phase 5 deprecation PR doesn't blindside it.

---

### BL-003
**Title:** Explicitly excluded product work (cross-device “What this builds”)  
**Status:** Out of scope (by current plan)  
**Theme:** client; server LLM; KG  
**Priority:** Low (until product re-opens)  
**Why it exists:** The plan states **out-of-product-scope** items so implementers do not “accidentally” build them in the same chunk.  
**Recommended follow-up:** If the product re-opens any line (e.g. offline tools, full LibreChat parity), file a new **explore** + plan rather than smuggling them into the cross-device line.  
**Original scope context:** In-scope: web + façade + PWA path; out-of-scope lines are **portable to future product**, not “forbidden forever”.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — `**Out of scope:**` bullet list (lines ~14)  

**Evidence snippets:**  
> **Out of scope:** local KG mirror, offline tool execution, native iOS/Android packaging, wholesale rewrite of LibreChat features beyond what Lumogis Web replaces, server-side LLM/router/MCP changes.

---

### BL-004
**Title:** Hosted / Phase C multi-tenant foundations (audit)  
**Status:** Deferred (by design)  
**Theme:** multi-user; security; deployment  
**Priority:** Low (until product pivot)  
**Why it exists:** The original audit’s **Phase C** (tenant_id, RLS, per-user Qdrant/Falkor, quotas, etc.) is **explicitly not** family-LAN work; it is recorded so it is **not lost** when the household scope is right.  
**Recommended follow-up:** If Lumogis moves toward **hosted** multi-tenant, new `/explore` + plan set; re-read `docs/private/MULTI-USER-AUDIT.md` Phase C.  
**Original scope context:** Family plan §3, D10, §16 *optional later* vs `MULTI-USER-AUDIT-RESPONSE` §4.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — “Deferred by design”, §3  
- `docs/private/MULTI-USER-AUDIT-RESPONSE.md` — Phase C all **out of scope** for family LAN  
- *(maintainer-local only; not part of the tracked repository)* — D10, §3 non-goals  

**Evidence snippets:**  
> All ten items … are **explicitly out of scope** for family LAN in `family_lan_multi_user.plan.md` §3, D10, and §16

---

### BL-005
**Title:** Optional LibreChat HMAC multi-user bridge (reference only)  
**Status:** Deferred (not planned)  
**Theme:** client; interop; auth  
**Priority:** Unknown  
**Why it exists:** If LibreChat were to become a **per-user** surface again, a bridge design is **preserved** in plan §23, but **no code is planned**.  
**Recommended follow-up:** Only if product reverses the “Lumogis Web first” decision; otherwise treat as **historical only**.  
**Original scope context:** D9 and former phases 3.5/4 **dropped**; §23 is reference.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — `## 23. Deferred / not planned`  
- `docs/decisions/012-family-lan-multi-user.md` — Why bridge deferred  

**Evidence snippets:**  
> **Status:** **DEFERRED / NOT PLANNED** … The content below is preserved for reference only. **No implementation work is planned.**

---

### BL-006
**Title:** JWT access-token revocation beyond TTL; multi-device sessions  
**Status:** Open question (documented v1 limit)  
**Theme:** auth; security  
**Priority:** Medium  
**Why it exists:** v1 is **stateless access JWT** + **single** refresh `jti`; “full” revocation and **multi-device** need schema (`token_version`, `refresh_tokens` table, or denylist).  
**Recommended follow-up:** If threat model or mobile refresh patterns require it, design a **follow-up plan**; align with `cross_device` cookie era.  
**Original scope context:** `family_lan` plan and ADR 012 “harder” consequences.  
**Sources:**  
- `docs/decisions/012-family-lan-multi-user.md` — Revisit + consequences; token TTL-only  
- *(maintainer-local only; not part of the tracked repository)* — Token model limitations  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group N  

**Evidence snippets:**  
> token revocation is otherwise TTL-only (≤15 min) until a `token_version` column is added  
> **Multi-device refresh** → `refresh_tokens` table

---

### BL-007
**Title:** Shared in-process rate limiter vs multi-`uvicorn` workers  
**Status:** Revisit when trigger fires  
**Theme:** deployment; performance  
**Priority:** Medium (when scaling)  
**Why it exists:** Rate limiting is in-process; **multi-worker** needs Postgres/Redis backing.  
**Recommended follow-up:** When `uvicorn --workers N` is chosen, port limiter to shared store per plan / ADR revisit.  
**Original scope context:** `family_lan` §19 interop, ADR 012.  
**Sources:**  
- `docs/decisions/012-family-lan-multi-user.md` — Revisit conditions, consequences  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group N  

**Evidence snippets:**  
> The orchestrator scales beyond a single uvicorn worker — move the in-process rate limiter to a shared backing store.

---

### BL-008
**Title:** Optional double-submit CSRF tokens (on top of `SameSite` + `Origin`)  
**Status:** Recommended later  
**Theme:** security; web  
**Priority:** Low  
**Why it exists:** v1 relies on **SameSite=Strict** + `Origin` check; double-submit tokens **deferred** as extra hardening.  
**Recommended follow-up:** Re-evaluate for **high-risk** cookie flows or external embedding.  
**Original scope context:** `family_lan` risks / CSRF notes.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — CSRF discussion  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group N “Double-submit”  

**Evidence snippets:**  
> `SameSite=Strict + Origin check` is sufficient for v1; **double-submit CSRF tokens** deferred.

---

### BL-009
**Title:** `resolve_runtime_credential` — move CalDAV, ntfy, LLM readers to tier-aware resolution  
**Status:** Recommended next step  
**Theme:** credentials; connectors; CalDAV; notifier; LLM  
**Priority:** High  
**Why it exists:** ADR **027** + **per-user credentials** 018 add **household/system** tables and `resolve_runtime_credential`, but some **adapters** still read **per-user table only** (e.g. CalDAV, ntfy, LLM paths) until follow-on chunks.  
**Recommended follow-up:** Migrate `services/caldav_credentials`, `ntfy_runtime.*`, LLM runtime in **independent** chunks; operator curl matrix in plan.  
**Original scope context:** Interoperability lists “not yet migrated” adapters.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — Interoperability / next steps  
- `docs/decisions/027-credential_scopes_shared_system.md` — Option C/D, consequences  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group B  

**Evidence snippets:**  
> **Recommended:** LLM → CalDAV → ntfy … `resolve_runtime_credential` into production paths

---

### BL-010
**Title:** External vault / 1Password-class broker (Option D) for advanced homelabs  
**Status:** Out of scope (v2) / deferred product  
**Theme:** credentials; deployment  
**Priority:** Low  
**Why it exists:** ADR 027 lists **opt-in** external vaults as **extra Docker weight** for default local-first; **deferred** until a product line needs it.  
**Recommended follow-up:** Plugin or optional **compose profile**; keep **ADR** alignment if pursued.  
**Original scope context:** `credential_scopes` alternatives.  
**Sources:**  
- `docs/decisions/027-credential_scopes_shared_system.md` — **Option D** as deferred  

**Evidence snippets:**  
> **Option D — external vault / broker** (Vault, 1Password Connect, etc.) — deferred / opt-in plugin for advanced homelabs

---

### BL-011
**Title:** `credential_scopes` operator smoke, audit docs, optional route split  
**Status:** Recommended next step (polish)  
**Theme:** credentials; ops; docs  
**Priority:** Medium  
**Why it exists:** Plan and arbitration call for **curl staging matrix**, `audit_log` + `input_summary.tier` **documentation sweep**, and optional `routes/connector_credentials.py` split.  
**Recommended follow-up:** Execute “Next steps” in `credential_scopes` plan tail.  
**Original scope context:** After migration **018** lands and tiers exist in DB.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — Test cases, Next steps, Interop  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group B optional critique  

**Evidence snippets:**  
> **Operator smoke:** curl matrix … **Docs sweep:** `audit_log.user_id` three-way overload + `input_summary.tier`

---

### BL-012
**Title:** Batch jobs — `routes/admin.py` `BackgroundTasks` to queue; operator projection and more  
**Status:** Deferred  
**Theme:** batch jobs; queueing; admin; audit  
**Priority:** High (admin paths); Medium (projection)  
**Why it exists:** ADR **025** defers **heavy** admin work (`/kg/trigger-weekly`, `/entities/deduplicate`, etc.) **onto** the job queue, plus **operator triage** API, **dead-job audit** events, **sidecar** worker, **round-robin** claim, **graph** job unification, and optional **Event** for dead jobs.  
**Recommended follow-up:** Use **Deferred follow-ups** section in `per_user_batch_jobs` plan; align with `Future chunks must know` in ADR 025.  
**Original scope context:** v1 is **in-process** worker; listed items are **trigger-based** follow-ups.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — `## Deferred follow-ups` and Next steps  
- `docs/decisions/025-per-user-batch-jobs.md` — “Future chunks”, Revisit conditions  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group C  

**Evidence snippets:**  
> The five current `BackgroundTasks` callsites … are first-class **migration candidates** and should be moved as part of v1 or as **named follow-up chunks**  
> If an **operator needs queue triage** … ship the deferred **admin** projection

---

### BL-013
**Title:** Procrastinate / Oban / third-party queue adoption (only if in-house cost hurts)  
**Status:** Revisit when trigger fires  
**Theme:** batch jobs; library evaluation  
**Priority:** Low (until pain)  
**Why it exists:** ADR 025 defers **Procrastinate** and notes **Oban** if **Python 3.12+** is committed; third-party only when custom maintenance exceeds comfort.  
**Recommended follow-up:** Re-read exploration when `user_batch_jobs` table becomes operationally hot.  
**Original scope context:** v1 = **Postgres** + in-repo worker.  
**Sources:**  
- `docs/decisions/025-per-user-batch-jobs.md` — Alternatives, defer Procrastinate  

**Evidence snippets:**  
> Defer until our custom-queue maintenance cost outgrows comfort.

---

### BL-014
**Title:** LLM per-user — dashboard UI, e2e integration, cache-invalidation / perf tail  
**Status:** Deferred (plan “Next steps”)  
**Theme:** LLM; UI; security  
**Priority:** High (UX); Medium (cache story)  
**Why it exists:** `llm_provider_keys` chunk shipped core resolver; plan **Pass 3.12** and **5.16** and **“future chunks”** (cross-process cache, optional byte cap) remain.  
**Recommended follow-up:** After Lumogis Web exists, **surface** in UI + **end-to-end** test pack.  
**Original scope context:** ADR 026 status history and plan tail.  
**Sources:**  
- `docs/decisions/026-llm-provider-keys-per-user.md` — “Deferred: dashboard…”, “follow-up if…”  
- *(maintainer-local only; not part of the tracked repository)* — Next steps  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group D  

**Evidence snippets:**  
> 4. **Deferred:** dashboard UI changes (plan Pass 3.12) and the `GET /v1/models` …  
> `POST /api/v1/admin/internal/invalidate-llm-cache` — long-term

---

### BL-015
**Title:** Household + system “credential health” and admin **enumeration** UX (LLM + broader)  
**Status:** Open question (ADR 026)  
**Theme:** credentials; admin; observability  
**Priority:** Medium  
**Why it exists:** ADR 026 notes follow-up for **per-user** visibility and **key health** for operators.  
**Recommended follow-up:** Define minimal **admin** surfaces once tier resolution is universal (with **027**).  
**Original scope context:** B13 / credential UX adjacent.  
**Sources:**  
- `docs/decisions/026-llm-provider-keys-per-user.md` — follow-up bullet on enumeration / health  

**Evidence snippets:**  
> admin "household key health" needs a per-user **enumeration** … follow-up

---

### BL-016
**Title:** Connector permissions — dashboard tile, stricter `get_connector_mode`, `scopes` column, multi-worker cache  
**Status:** Mixed — recommended next (UX) + revisit (scale)  
**Theme:** multi-user; permissions; deployment  
**Priority:** Medium  
**Why it exists:** A2 table work **shipped**; UX tile and **per-process** cache in multi-**worker** and **per-action scopes** array remain follow-ups.  
**Recommended follow-up:** Implement per `per_user_connector_permissions` plan **Next steps**; on multi-worker, **LISTEN/NOTIFY** or TTL.  
**Original scope context:** ADR 024 consequences + `cross_device` tie-in.  
**Sources:**  
- `docs/decisions/024-per-user-connector-permissions.md` — Cache drift, Revisit, deferred follow-up #1–2  
- *(maintainer-local only; not part of the tracked repository)* — Recommended next steps  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group E  

**Evidence snippets:**  
> **revisit** if the orchestrator goes **multi-worker**  
> **Future** per-`(user, connector, action_type)` scope expansion (**deferred follow-up #1**)

---

### BL-017
**Title:** `per_user_backup_export` — un-skip CSRF tests; `per_user_account_erasure`; 410/507 follow-ups  
**Status:** Deferred  
**Theme:** export; privacy; web auth  
**Priority:** High (CSRF with cookies); Medium (erasure)  
**Why it exists:** Browser **cookie** auth and **CSRF** are **D11**-linked; **erasure** chunk split for scope; **streaming/507** cap in open questions.  
**Recommended follow-up:** Un-skip when **Lumogis Web** + **Origin/CSRF** are active; new plan for **GDPR-style erasure**.  
**Original scope context:** B8 shipped export **substrate**; not full **lifecycle** story.  
**Sources:**  
- `docs/decisions/016-per-user-backup-export.md` — `per_user_account_erasure`, `cross_device` un-skip  
- *(maintainer-local only; not part of the tracked repository)* — Follow-ups for downstream chunks  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group H, cross-ref A  

**Evidence snippets:**  
> `cross_device_lumogis_web` will … **un-skip** the deferred **CSRF** blocking tests  
> `per_user_account_erasure` **chunk** — follow-up

---

### BL-018
**Title:** `include_credentials=true` opt-in (backup transport) – rejected v1, revisit on real cross-instance use  
**Status:** Revisit if trigger  
**Theme:** security; export  
**Priority:** Low  
**Why it exists:** 016 documents why **opt-in** credential re-export is **rejected** for v1; **revisit** if operator need appears.  
**Original scope context:** `Alternatives` in 016.  
**Sources:**  
- `docs/decisions/016-per-user-backup-export.md` — Alternatives considered  

**Evidence snippets:**  
> Revisit only if a concrete **cross-instance migration** use case appears

---

### BL-019
**Title:** MCP `lmcp_` map — KG service gateway, expiry, per-tool `scopes`, admin/forensic surfaces  
**Status:** Deferred (plan OUT OF SCOPE; KG mirror)  
**Theme:** MCP; security; knowledge graph  
**Priority:** High (KG shared-token removal)  
**Why it exists:** B10 **orchestrator** work shipped; **KG** `/mcp` **mirror** still on legacy shared **token** per plan D1; plus **MCP** token lifecycle and **scope** depth.  
**Recommended follow-up:** **Core-owned gateway** in front of KG **FastMCP**; align with ADR 017 + `mcp_token` plan.  
**Original scope context:** 017 *finalised* Core; KG **intentionally** left for separate chunk.  
**Sources:**  
- `docs/decisions/017-mcp-token-user-map.md` — KG out of **scope** in plan (pointer to chunk)  
- *(maintainer-local only; not part of the tracked repository)* — OUT OF SCOPE, Interop  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group G  

**Evidence snippets:**  
> The **KG** service `/mcp/*` mirror is **out of scope** (plan D1): KG keeps its **legacy** shared-token gate **until a future** Core-owned gateway **chunk**  
> `expires_at` enforcement + optional **mint** API field

---

### BL-020
**Title:** Per-user connector credentials — MCP client/doc sweep, web UX, reveal, cache, sealed backup, per-user DEK  
**Status:** Deferred (substrate follow-ups)  
**Theme:** credentials; MCP; security; UX  
**Priority:** Medium (varies by line)  
**Why it exists:** 018 is **substance**; plan lists **mcp_client_per_user_compat**, first-party **UX** beyond scripts, **rate-limited** reveal, `resolve` **caching**, **sealed** backup, **per-user** encryption keys.  
**Recommended follow-up:** Triage **Deferred follow-ups** in `per_user_connector_credentials` plan in order of operator pain.  
**Original scope context:** 018+020+021 **shipped** plumbing; some items **intentionally** not in 018.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — Deferred follow-ups  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group F  

**Evidence snippets:**  
> **`mcp_client_per_user_compat` sweep** … `resolve()` **caching** (hot-path optimisation) … **Admin sealed backup** … **Per-user DEK** / envelope encryption

---

### BL-021
**Title:** `personal`/`shared`/`system` — Docker-compose integration for skipped scenarios; ADR-revisit list  
**Status:** Deferred (testing + triggers)  
**Theme:** memory scopes; deployment; Qdrant; audit  
**Priority:** Medium  
**Why it exists:** Plan/ADR list **S12–S14**-class tests behind compose and **revisit** triggers (Qdrant index, bulk-share, audit projection, graph **perf** on bulk publish).  
**Recommended follow-up:** Bring **headline** compose scenarios in-line with plan; track **revisit** bullets in ADR 015.  
**Original scope context:** B6 **shipped**; hardening and **scale** are next.  
**Sources:**  
- `docs/decisions/015-personal-shared-system-memory-scopes.md` — Revisit, constraints  
- *(maintainer-local only; not part of the tracked repository)* — Next steps  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group I  

**Evidence snippets:**  
> **Docker-compose** integration for skipped headline scenarios: `test_s12_*` …

---

### BL-022
**Title:** Notion-style ACL, `'public'`, hosted RLS, cross-graph / entity-coref — from memory-scope ADR  
**Status:** Revisit when (conditions in ADR)  
**Theme:** memory scopes; multi-tenant; graph  
**Priority:** Low (until product signals)  
**Why it exists:** ADR 015 lists **revisit** paths that are **not** v1 (ACL granularity, new scope literal, RLS, Falkor **cross-graph**, entity **coref** at scale).  
**Recommended follow-up:** If household feedback or **hosted** mode demands it, **explore** with explicit new ADR amend.  
**Original scope context:** v1: **3-way** `scope` + **visibility** helpers.  
**Sources:**  
- `docs/decisions/015-personal-shared-system-memory-scopes.md` — `## Revisit conditions`  

**Evidence snippets:**  
> if households consistently **report** needing per-person **granularity** after v1 → revisit **Notion**-style ACL  
> **Hosted-tenant pivot** — if Lumogis ever runs as hosted multi-tenant, revisit **Postgres RLS**

---

### BL-023
**Title:** File index / ingest — test-debt env failures, pre-namespace backfill, strict sessions pairing, `audio_memos` namespace  
**Status:** Follow-up (plan + out-of-scope notes)  
**Theme:** file index; Qdrant; sessions  
**Priority:** Medium (test); Medium (operational backfill)  
**Why it exists:** ADR 013 and plan reserve **backfill/operator** work and **test-debt** sweep; **audio_memos** deferred to route; **strict** `sessions` ↔ Qdrant pairing is **non**-goal until chat memory is stricter.  
**Recommended follow-up:** Own **verify-log** 14 test failures in separate pass; add **backfill** runbook.  
**Original scope context:** B11/B12 **shipped** core; operational **comfort** and **edge** routes remain.  
**Sources:**  
- `docs/decisions/013-per-user-file-index-and-ingest-attribution.md` — Out of scope, Revisit  
- *(maintainer-local only; not part of the tracked repository)* — Next steps, Implementation log  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group J  

**Evidence snippets:**  
> `audio_memos` per-user **namespace** — no live **writer** exists; **deferred** to whichever chunk **lands the audio-capture** route

---

### BL-024
**Title:** `entity_relations` — sibling tables / per-timeline evidence if product needs fine-grained metadata  
**Status:** Revisit when (ADR 014)  
**Theme:** knowledge graph; entities  
**Priority:** Low (until product needs timeline UI)  
**Why it exists:** 014’s **revisit** conditions: **sibling** `entity_observations` for **re-observation** trails, **granularity** in UNIQUE if extractors get finer, SQL if `ON CONFLICT` deprecated.  
**Recommended follow-up:** If **evidence-timeline** UI is requested, new table **instead of** breaking UNIQUE.  
**Original scope context:** Dedup chunk **shipped**; this is **stability of contract** for future.  
**Sources:**  
- `docs/decisions/014-entity-relations-evidence-dedup.md` — `## Revisit conditions`  

**Evidence snippets:**  
> the right move is a new `entity_observations` **sibling** table — NOT relaxing this **UNIQUE**

---

### BL-025
**Title:** CalDAV credentials — `Signal.url` dedupe, per-user lookahead, URL single-source-of-truth, UI, test connection, empty-credentials  
**Status:** Open question / follow-up in plan  
**Theme:** CalDAV; connectors; cost  
**Priority:** Medium  
**Why it exists:** 021 and plan have **D11–D13** class items not fully in first chunk.  
**Recommended follow-up:** Close **open questions** in `caldav_connector_credentials` plan.  
**Original scope context:** B4 **rollout**; **savings/UX** iterations stay open.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group K  
- *(maintainer-local only; not part of the tracked repository)* — Open questions / D-section  

**Evidence snippets:**  
> **`Signal.url` + dedupe** for CalDAV to **cut** LLM cost

---

### BL-026
**Title:** Structlog package sharing to KG / stack-control; JSONB for audit; `request_id` on hot 401  
**Status:** Incremental (audit logging follow-ups)  
**Theme:** observability; audit; deployment  
**Priority:** Medium  
**Why it exists:** 019 is **in**; **tighter** DTO/query paths are **revisit** / plan **next** items.  
**Recommended follow-up:** `structured_audit_logging` plan **Next steps** and ADR 019 `Revisit` bullets.  
**Original scope context:** B14 = **shipped** baseline; these are **scale**-triggered.  
**Sources:**  
- `docs/decisions/019-structured-audit-logging.md` — `## Revisit conditions`  
- *(maintainer-local only; not part of the tracked repository)* — Next steps, Recommended  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group L  

**Evidence snippets:**  
> **JSONB** migration for **audit** summary columns when **query** workloads justify

---

### BL-027
**Title:** Credential management dashboard — manual smokes, `register()` helper, JSON **schema** hints in modal  
**Status:** Incremental (UX)  
**Theme:** credentials; UI; DX  
**Priority:** Medium  
**Why it exists:** 020 and plan have **interoperability** and **open** items *after* core dashboard exists.  
**Recommended follow-up:** `credential_management_ux` plan `Open` + `Interoperability` tails.  
**Original scope context:** 020 **final**; not every connector **polish** item.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group M  
- *(maintainer-local only; not part of the tracked repository)* — Open / Interop (where present)  

**Evidence snippets:**  
> `register(name, description)` **helper** when many connectors **land** … **JSON** schema **hints** in **modal**

---

### BL-028
**Title:** ntfy — remove legacy 410 body; **multi-orchestrator-worker** ntfy fanout  
**Status:** Deferred (operational)  
**Theme:** notifier; deployment  
**Priority:** Medium (multi-worker)  
**Why it exists:** `ntfy` runtime ADR and `connect-and-verify` list **deferred** items when more than one worker / legacy compat.  
**Recommended follow-up:** Follow `connect-and-verify` “deferred” notes and 022 `Revisit`.  
**Original scope context:** B5 **per-user** shipped.  
**Sources:**  
- `docs/connect-and-verify.md` — lines ~607, ~1139+ (deferred)  
- `docs/decisions/022-ntfy-runtime-per-user-shipped.md` — `## Revisit conditions`  

**Evidence snippets:**  
> 410 **detail** body. Slated for **removal** in a follow-up **release** …  
> **multi-worker**. **Tracked** as **deferred** follow-up

---

### BL-029
**Title:** Ecosystem / MCP — `stateful` long-running **MCP** in separate capability (ADR 010)  
**Status:** By design (future)  
**Theme:** MCP; architecture  
**Priority:** Low (until a feature needs it)  
**Why it exists:** Community tools stay **stateless**; a **separate** service would own **stateful** MCP.  
**Recommended follow-up:** New service only if a **concrete** product needs server→client and **session**-bound tools.  
**Original scope context:** 010 **plumbing** scope choice.  
**Sources:**  
- `docs/decisions/010-ecosystem-plumbing.md` — `Stateless` vs future **stateful**  

**Evidence snippets:**  
> a **future** **stateful** MCP **surface** … **belongs** in a **separate** capability service

---

### BL-030
**Title:** `DEFAULT_USER` → per-token (ADR 010 — historical note)  
**Status:** Open question (superseded in part by 017)  
**Theme:** MCP; auth  
**Priority:** Low  
**Why it exists:** 010’s narrative points at **MCP** token mapping; **017** is now the locus.  
**Recommended follow-up:** When editing 010, cross-link **only**; **work** is BL-019 and BL-020.  
**Original scope context:** Ecosystem **origin**; **B10** is **separate** finalised path.  
**Sources:**  
- `docs/decisions/010-ecosystem-plumbing.md` — `### _DEFAULT_USER_ID`  

**Evidence snippets:**  
> A **future** **migration** to: … **MCP** token user **mapping** … (superseded by 017; keep for history)

---

### BL-031
**Title:** Graph service extraction — remove in-**Core** `plugins/graph` (cleanup plan); KG `graph_projection_state` (phase-2)  
**Status:** Follow-up (ADR 011 consequences)  
**Theme:** knowledge graph; deployment; data ownership  
**Priority:** Medium (cleanup); lower (schema)  
**Why it exists:** 011 says **plugin** stays in Core for **one release**; **cross-**writes to Core tables are **debt**; **state** should move to **KG**-owned table.  
**Recommended follow-up:** Execute **extraction** plan’s **post**-ship cleanup when **service** mode is default enough.  
**Original scope context:** 011 **finalised** with accepted **debt** bullets.  
**Sources:**  
- `docs/decisions/011-lumogis-graph-service-extraction.md` — `### Negative` (duplication, cross-writes)  

**Evidence snippets:**  
> A **KG-owned** `graph_projection_state` table is the planned **phase-2** fix.  
> **Plugin** code … **its removal** is a **follow-up** plan

---

### BL-032
**Title:** `capability_launchers` test debt — `test_capability_health.py` vs **lifespan** auto-discovery  
**Status:** Test debt (named topic)  
**Theme:** plugins; test  
**Priority:** Low  
**Why it exists:** Family and scopes plan **Implementation** logs + audit response **6 failing tests** 4+2 split still **owned** by `capability_launchers_and_gateway` + `lumogis_graph` extraction.  
**Recommended follow-up:** Fix in **owning** plan topic; not random PR scope.  
**Original scope context:** `family_lan` explicitly **out of scope** for that debt.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group O; consolidated doc intro  
- *(maintainer-local only; not part of the tracked repository)* — failing tests, deferred topics  

**Evidence snippets:**  
> 4 in `test_capability_health.py` **owned** by `capability_launchers_and_gateway`

---

### BL-033
**Title:** `lumogis_graph` extraction test debt — `TestBackfillEndpoint` / reconcile tests stale paths  
**Status:** Test debt  
**Theme:** knowledge graph; test  
**Priority:** Low  
**Why it exists:** Same as BL-032 — **2** tests under **extraction** topic, paths moved.  
**Recommended follow-up:** Repair or remove in **extraction** maintainer pass.  
**Original scope context:** Stale references to `orchestrator/plugins/` in tests.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — Group O  
- `docs/decisions/012-family-lan-multi-user.md` — 6 test failures (historical)  

**Evidence snippets:**  
> 2 in `TestBackfillEndpoint` **owned** by `lumogis_graph_service_extraction`

---

### BL-034
**Title:** **Private** audit follow-ups (filesystem, Splink, S2S identity) — *no* dedicated plan file yet  
**Status:** Open question (stakeholder tracking)  
**Theme:** security; file ingest; graph quality; observability  
**Priority:** Low until operator pain  
**Why it exists:** `MULTI-USER-AUDIT-RESPONSE` still lists some **prose** audit **rows** (shared `FILESYSTEM_ROOT` / “workspace”, Splink **model** path, **S2S** `user_id` on **LiteLLM**/Ollama/KG hops).  
**Recommended follow-up:** If pain reports arrive, new **`/explore`**.  
**Original scope context:** Not the **ten**-row **A/B/C** table; “outside tables” in response doc **§5**.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` — “Group P — no plan yet”  
- `docs/private/MULTI-USER-AUDIT-RESPONSE.md` — §5 + §5.5 surrounding  

**Evidence snippets:**  
> Shared `FILESYSTEM_ROOT` / **workspace** … **Splink** path  
> **Service**-**to**-**service** **per-user** identity

---

### BL-035
**Title:** `news_aggregation` — commercial / crawler / reader integrations beyond signal query  
**Status:** Deferred (draft ADR)  
**Theme:** news; signals; product  
**Priority:** Low  
**Why it exists:** Draft says **crawler**-scale and **Miniflux**-class external readers are **out** of default core; **API** adapters and **revisit** on **RSS** ecosystem health.  
**Recommended follow-up:** `Phase 4`+ **“briefing”** style features should **reuse** signal primitive (ADR text).  
**Original scope context:** `signals` **already** the SoR.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — scope + `## Revisit conditions`  
- `docs/lumogis_kg_quality_strategy.md` (Product roadmap line references **news** in broader roadmap context — cross-check only)  

**Evidence snippets:**  
> Defer **large**-**scale** **crawler**-**based** aggregation to **out**-**of**-**scope** / **separate** **products**  
> If **major** **publishers** **withdraw** **RSS** … **revisit** **API** adapter **priority**

---

### BL-036
**Title:** `conversational_voice` — PWA vs native, core WebSocket, optional `TextToSpeech` port  
**Status:** Revisit (draft ADR)  
**Theme:** voice; client; core  
**Priority:** Low (until product prioritises)  
**Why it exists:** Draft defers some paths until **browser** and **core** **streaming** are stable.  
**Recommended follow-up:** ADR’s **revisit** triggers; do not fork voice **in parallel** to `signals`.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — `Future chunks` + `## Revisit conditions`  

**Evidence snippets:**  
> If **core** **gains** **native** **WebSocket** **voice** … **revisit** unification

---

### BL-037
**Title:** `deep_research` — **sandbox** ADR for code-execution, STORM, round caps, local-LLM limits  
**Status:** Open (draft ADR) + revisit  
**Theme:** research; tool loop; security  
**Priority:** Medium (when research mode is productised)  
**Why it exists:** Draft defers **user-controlled** **code** to a **separate** **sandbox** **ADR**; `STORM`; round caps.  
**Recommended follow-up:** Lock **web_search** + **max rounds** invariants before a **“research mode”** ship.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — `Revisit` + `Future`  

**Evidence snippets:**  
> If **user**-**controlled** **code** **execution** is **required**, **revisit** with a **sandbox** **ADR** — not this **decision**

---

### BL-038
**Title:** `web_search` tool plan — SearXNG **compose** profile, dashboard subsection, `WEB_SEARCH_PERFORMED` hook, per-user settings  
**Status:** Out of plan / follow-up (implemented core per plan, tails remain)  
**Theme:** web search; tool; deployment  
**Priority:** Low–Medium (by sub-item)  
**Why it exists:** `web_search` plan lists **optional** compose **profile**, **Ask/Do** dashboard subsection, **Event** for plugins, and **per-user** **settings** — all **out** of **v1** in that file.  
**Recommended follow-up:** `follow-up` or **new** plan when SearXNG in stack or analytics needed.  
**Original scope context:** v1: **port** + **backends** + gating.  
**Sources:**  
- *(maintainer-local only; not part of the tracked repository)* — `Optional Compose`, `## Out of scope (follow-ups)`, OQ4  

**Evidence snippets:**  
> A `docker-compose.searxng.yml` **overlay** … can **ship** **later**; out of **scope** for this **plan**  
> add a **hook** in a **follow-up** if a **plugin** needs **structured** access

---

### BL-039
**Title:** `home_automation` / `voice_input` / `notifier_*` / `knowledge_graph_visualization` (draft) — “when prioritised” roadmap  
**Status:** Open question (pre-final ADR backlog)  
**Theme:** (varies) home automation, voice, notifier UX, graph viz  
**Priority:** Low  
**Why it exists:** **Draft** ADRs in *(maintainer-local only; not part of the tracked repository)* and matching **exploration**s record **revisit** / **out**-**of**-**scope** lines. Not all are **final** `docs/decisions/`; treat as **candidates** for future work.  
**Recommended follow-up:** For each, confirm **/create**-**plan** or **/record-**-**retro** per skill workflow.  
**Original scope context:** Ideation / **gaps** before ship.  
**Sources:** (representative)  
- *(maintainer-local only; not part of the tracked repository)* — `## Revisit conditions`  
- *(maintainer-local only; not part of the tracked repository)* (if `grep` found defer)  
- *(maintainer-local only; not part of the tracked repository)* / `per_user_notifier_targets.md` — per-user **notifier** product gaps  

> **Note:** Full text not re-quoted here; each file has 1–3 `revisit` or `out of scope` bullets. Use those files as the **authoritative** wording.

---

### BL-040
**Title:** `lumogis_kg_quality_strategy` — drift **detection** (component 7) when real operator data exists  
**Status:** Deferred (strategy doc)  
**Theme:** knowledge graph; quality; ops  
**Priority:** Low until calibration data exists  
**Why it exists:** The strategy **explicitly** defers **drift** to **not** use **synthetic** data.  
**Recommended follow-up:** Re-read §8 when **Lumogis** has long-lived **families** in prod-like runs.  
**Original scope context:** `docs/lumogis_kg_quality_strategy.md` is **not** a final ADR.  
**Sources:**  
- `docs/lumogis_kg_quality_strategy.md` — `## 8. Open Questions` and component 7  

**Evidence snippets:**  
> Component 7 (drift detection) should be **deferred** until the user has **real** **operational** data

---

### BL-041
**Title:** `lumogis_kg_quality` — NER / spaCy calibration; typed **REBEL** relations at Phase 5+  
**Status:** Revisit (strategy doc)  
**Theme:** NLP; quality; graph  
**Priority:** Low (until model swap)  
**Why it exists:** `Open Questions` + **8.2** in same doc.  
**Recommended follow-up:** Re-weight composite score if **spancat** or new **NER** lands.  
**Sources:**  
- `docs/lumogis_kg_quality_strategy.md` — `### 8.1`, `8.2`  

**Evidence snippets:**  
> **spaCy** **confidence** calibration is a **practical** **blocker**  
> Defer **until** **Phase 5** or when **local** **LLM** **quality** improves

---

### BL-042
**Title:** Technical **debt** register — `GET /graph/stats` `user_id="default"` filter  
**Status:** Open (DEBT)  
**Theme:** graph; multi-user; security  
**Priority:** Medium (when `Phase 6` or multi-tenant)  
**Why it exists:** `DEBT` says Falkor count **hardcodes** `default` until **multi-tenant** isolation.  
**Recommended follow-up:** Parameterize **Cypher** with **auth** `user_id` like Postgres fields.  
**Original scope context:** Same doc says Postgres parts already **per-user** on that **response**.  
**Sources:**  
- `docs/decisions/DEBT.md` — “Graph stats” section  

**Evidence snippets:**  
> the stats endpoint **counts** graph **nodes** with a **Cypher** filter **scoped** to `user_id = "default"` … must use the same `user_id` as the rest of the request

---

### BL-043
**Title:** `sync`/`async` end-to-end migration of Core (DEBT 0.3.0rc1 + **ADR 010** pointer)  
**Status:** Revisit (trigger: multi-user or scale)  
**Theme:** architecture; performance; migration  
**Priority:** Low until trigger  
**Why it exists:** `capability_registry` was first **async**; **debt** plans **5-step** **migration** to **async** ports.  
**Recommended follow-up:** Do **not** start for **fun**; trigger per **debt** doc.  
**Original scope context:** ADR 010 and **debt** cross-reference.  
**Sources:**  
- `docs/decisions/DEBT.md` — “Sync/async consistency”  
- `docs/decisions/010-ecosystem-plumbing.md` — `known-technical-debt` pointer  

**Evidence snippets:**  
> A **coordinated** **migration** should be **triggered** by the **first** **multi-**-**user** **deployment** **requirement**

---

### BL-044
**Title:** `review_queue` — **B9** shipped **decide** path; `GET` listing “admin” posture called out in ADR/response  
**Status:** Intentional scope line (not a “bug defer”)  
**Theme:** approval flow; admin UX  
**Priority:** Unknown (product)  
**Why it exists:** Response doc states **`GET` listing** is **out** of B9; may still be a **product** want later.  
**Recommended follow-up:** If **non-**-**admin** “inbox for **my** pending” is needed, new **small** plan.  
**Original scope context:** 023 is **B9** **decide** **scope** as-built.  
**Sources:**  
- `docs/private/MULTI-USER-AUDIT-RESPONSE.md` — B9 row, **GET** **listing** line  
- `docs/decisions/023-review-queue-per-user-approval.md` — (heading context)  
- `docs/decisions/024-per-user-connector-permissions.md` (audit narrative cross-links)  

**Evidence snippets:**  
> **`GET` /review**-**queue** **listing** remains **admin**-**oriented** and was **explicitly** **out** of **scope** for B9

---

### BL-045
**Title:** **027** and **multi-tenant** pivot (household / system key dimension + **identity** columns)  
**Status:** Revisit when (ADR)  
**Theme:** credentials; multi-tenant  
**Priority:** Low (until product hosted)  
**Why it exists:** 027’s **revisit** includes **true** **multi-**-**tenant** and whether **household** **id** and **tiers** need **convergence** with `memory` **scope**.  
**Recommended follow-up:** Re-open only with **separate** **/explore**; do **not** conflate with **household** **LAN**.  
**Sources:**  
- `docs/decisions/027-credential_scopes_shared_system.md` — `## Revisit conditions` first bullet  

**Evidence snippets:**  
> if Lumogis **pivots** to true **multi-**-**tenant** **hosting** — **revisit** whether `household_id` … and whether memory `scope` and credential `tier` should **converge`

---

## Grouped by Theme

- **Client / A1 / cross-device** — BL-001, BL-002, BL-003, (BL-017, BL-019 CSRF/KG tie-ins).  
- **Auth / security / web hardening** — BL-004, BL-006, BL-007, BL-008, BL-017, BL-019 (MCP/KG), BL-028, BL-044, BL-045.  
- **Multi-user, LAN vs hosted, audit Phase C** — BL-004, BL-010, BL-016, BL-022, BL-023, BL-024, BL-040, BL-045.  
- **Credentials and permission tiers (018/020/021/027)** — BL-009–BL-011, BL-015, BL-016, BL-020, BL-025, BL-027, BL-045.  
- **Batch jobs and queueing (025)** — BL-012, BL-013.  
- **MCP, connectors, CalDAV, notifier** — BL-019, BL-020, BL-025, BL-028, BL-029, BL-030, BL-032, BL-038.  
- **Export / import / account lifecycle** — BL-017, BL-018, BL-044.  
- **UI / dashboard / credential UX** — BL-014, BL-016, BL-019, BL-020, BL-025, BL-027.  
- **LLM and chat tooling** — BL-009, BL-014, BL-015, BL-037, BL-038.  
- **Memory scopes (015)** — BL-021, BL-022.  
- **File index, ingest, file paths (013)** — BL-023.  
- **Entity relations / graph quality (014, kg strategy)** — BL-024, BL-040, BL-041, BL-042, BL-031, BL-035.  
- **Knowledge graph service, extraction, plugins (011, 010)** — BL-019, BL-029, BL-031, BL-032, BL-033.  
- **Audit / structured logging (019)** — BL-026.  
- **Deployment / workers / Caddy** — BL-001, BL-004, BL-007, BL-012, BL-016, BL-013, BL-028, BL-029.  
- **Backlog and roadmap (drafts: news, voice, home, quality strategy)** — BL-032, BL-033, BL-034, BL-035, BL-036, BL-037, BL-038, BL-039, BL-040, BL-041.  
- **LibreChat, ecosystem** — BL-005, **BL-003** product exclusions, `010` (BL-029, BL-043).  
- **Technical debt (DEBT.md)** — BL-042, BL-043.  

> Overlap in lists is **intentional** — themes are a **view**, not partitions.

## Grouped by Source File

| Source file | # items (approx.) | BL IDs |
|-------------|:-----------------:|--------|
| `docs/private/MULTI-USER-AUDIT-RESPONSE.md` | 6+ | 001, 004, 005, 016, 034, 044 |
| `docs/private/MULTI-USER-AUDIT-PLANS-NEXT-STEPS.md` | 20+ (meta index) | most BL items; see Groups A–P in that file |
| `docs/private/MULTI-USER-AUDIT.md` | (via response) | 004 (Phase C) |
| `docs/decisions/012-*.md` | 3+ | 005, 006, 012 (narrative) |
| `docs/decisions/010-*.md` | 2+ | 029, 030, 043 |
| `docs/decisions/011-*.md` | 1+ | 031 |
| `docs/decisions/013-014-016-*.md` (per-ADR) | 5+ | 017, 018, 023, 024 |
| `docs/decisions/015-*.md` | 2+ | 021, 022 |
| `docs/decisions/017-*.md` | 1+ | 019 |
| `docs/decisions/018-021-*.md` | 4+ | 020, 025 |
| `docs/decisions/022-*.md` | 1+ | 028 |
| `docs/decisions/024-027-*.md` | 5+ | 009–011, 016, 020, 024, 030, 045 |
| `docs/decisions/025-*.md` | 2+ | 012, 013 |
| `docs/decisions/026-*.md` | 2+ | 014, 015 |
| `docs/decisions/DEBT.md` | 2+ | 042, 043 |
| `docs/connect-and-verify.md` | 1+ | 028 |
| `docs/lumogis_kg_quality_strategy.md` | 2+ | 040, 041 |
| *(maintainer-local only; not part of the tracked repository)* | 2+ | 001, 002, 003 |
| *(maintainer-local only; not part of the tracked repository)* | 3+ | 004, 005, 006, 008, 032, 033 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 025 | 2+ | 012, 013 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 026 | 2+ | 014, 015 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 027 | 2+ | 009, 010, 011 |
| *(maintainer-local only; not part of the tracked repository)* | 1+ | 019, 020 |
| *(maintainer-local only; not part of the tracked repository)* | 1+ | 020 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 016 | 1+ | 017, 018 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 015 | 1+ | 021, 022 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 021 | 1+ | 025 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 019 | 1+ | 026 |
| *(maintainer-local only; not part of the tracked repository)* / ADR 020 | 1+ | 027 |
| *(maintainer-local only; not part of the tracked repository)* | 1+ | 038 |
| *(maintainer-local only; not part of the tracked repository)* (news, deep_research, conversational_voice, home) | 4+ | 035, 036, 037, 039 |

(Counts are **approximate** when one file is cited in many BL **Sources** blocks.)

## Needs confirmation

1. **BL-039 (draft-ADR bundle)** — Draft ADRs in *(maintainer-local only; not part of the tracked repository)* are not at a uniform lifecycle stage. Before scheduling work, re-read the matching `exploration` and the topic row in *(maintainer-local only; not part of the tracked repository)* (per skill flow). The files named in BL-039 are only pointers; each ADR is authoritative when updated.

2. **`cross_device` open-question list vs. arbitration renames** — The plan is long. This backlog points to the plan’s *Open questions* section rather than copying every row. If a question was resolved in review chat but the plan body was not updated, the plan may be stale until edited.

3. **ADR 012 / `LUMOGIS_PUBLIC_ORIGIN` / CSRF** — Some narrative in older plan history may conflict with a later `family_lan` hardening pass. Reconcile with current code and `docs/decisions/012-family-lan-multi-user.md` status history; do not re-open a closed gap without verification.

4. **“~14 test failures” (`per_user_file_index` implementation log) vs. current CI** — Treat the **count** as possibly stale until re-run; keep the **principle** (environmental / topic-owned test debt) as backlog-worthy per source docs.

5. **ntfy 410 body and multi-orchestrator-worker ntfy** — `docs/connect-and-verify.md` points at these; confirm line-level behaviour and priority against `docs/decisions/022-ntfy-runtime-per-user-shipped.md` and current `main` before scoping a chunk.

6. **`bootstrap_admin_first_login_rotation` and `/api/v1/me/password` verify** — Named in `cross_device` arbitration (e.g. as follow-up work). Confirm they still appear as open items in the current plan and codebase before filing issues.

## Excluded from this Backlog

- **Closed by later status history** — e.g. ADR 014’s `restore_path_idempotent_inserts` follow-up was explicitly closed when the restore path was verified; it is not listed as deferred.

- **Cursor skill templates** under *(maintainer-local only; not part of the tracked repository)* — generic “out of scope / open questions” *section labels* in `/create-plan` and `/explore` are workflow rubrics, not product backlog.

- **Roadmap narrative without a concrete later action** — e.g. root `README.md`: spot-checked; no stand-alone follow-up was extracted.

- **Pure implementation detail** (refactors, variable names) with no stated future or deferred action.

- **One-off “verify at merge” or pre-merge checklists** that do not describe a standing product or operational gap after the merge.

---

*File generated: 2026-04-22. Regenerate with a new repo-wide sweep if ADRs, plans, or the audit companion move materially.*
