# Web roadmap reconciliation — after self-hosted architecture remediation

> Status: Needs update  
> Last reviewed: 2026-05-06  
> Verified against commit: 37cf965  
> Notes: Original narrative dated **2026-04-26** below; **§8 discovery bullets were wrong for current HEAD** (PWA / Push / Capture shipped per extraction docs). Prefer **[`product-roadmap-reconciliation-audit-2026-05-02.md`](product-roadmap-reconciliation-audit-2026-05-02.md)** plus Phase **2–5** extraction files for “what shipped vs open”; treat this file as reconciliation **history + phase-disambiguation**, not the freshest backlog table.

**Slug:** `lumogis_web_roadmap_reconciliation_after_remediation`  
**Date:** 2026-04-26  
**Kind:** Planning / reconciliation doc; product updates may be noted here when they close tracked gaps (e.g. password management foundation, 2026-04).

**Sources:** `cross_device_lumogis_web.plan.md` (parent), `lumogis_web_admin_shell.plan.md` (child), `lumogis-self-hosted-platform-remediation-plan.md`, Phase 4/5 closeout reviews, `tool-vocabulary.md`, `docs/decisions/034-agent-harness-foundation-terminology-and-boundaries.md`, `clients/lumogis-web/README.md`, `App.tsx`, `openapi.snapshot.json`, `.cursor/follow-up-portfolio.md` (read-only for this document).

**Naming collision (read this first):** The **self-hosted remediation programme** uses “Phase 4 / Phase 5” for *household-control surfaces* and *capability scaffolding* (see remediation plan §3). The **parent** `cross_device_lumogis_web` plan uses “Phase 4 / Phase 5” for *Web Push + approval notifications* and *capture-from-anywhere*. Those are **different programmes**; completion of remediation Phase 4/5 does **not** mark the parent’s Phase 4/5 complete.

---

## Executive summary

- **Done:** Parent **Phase 0** (v1 façade + OpenAPI) and **Phase 1** (Lumogis Web core + Caddy same-origin) remain the verified baseline. **Child plan** `lumogis_web_admin_shell` remains **product-complete** for Me/Admin shells; optional test/CI gaps (e.g. CI for Playwright) are **non-blocking**.
- **Superseded or materially advanced by remediation:** Remediation **Chunk 6 / Phase 4 (household control)** delivered typed `GET` façades and Web views: `/api/v1/me/tools` → `/me/tools-capabilities`, `/api/v1/me/llm-providers` → `/me/llm-providers`, `/api/v1/me/notifications` → `/me/notifications`, `/api/v1/admin/diagnostics` → `/admin/diagnostics`. This **closes the intent** of several **child-plan follow-up rows** that assumed those surfaces did not exist (e.g. `lumogis_me_llm_providers_view` as a separate typed route — it now exists). **`/me/notifications`** combines the **read-only** channel/status façade (`GET /api/v1/me/notifications`) with **Phase 4C** browser Web Push enrolment (`PushOptIn`) — see `clients/lumogis-web/README.md`. **Remediation Phase 5** (capability scaffolding, mock capability, OOP audit, permission-labelled catalog) is **complete** as a *platform* slice; it does **not** substitute the **parent** programme’s Phase 4/5 labels (different meanings — see naming collision).
- **Shipped in-repo since this doc’s original snapshot (verify via extraction docs):** Parent **Phase 2** mobile UX — [cross-device-web-phase-2-mobile-ux-plan.md](cross-device-web-phase-2-mobile-ux-plan.md) (**implemented**); **Phase 3** PWA spine — `clients/lumogis-web/src/pwa/` (`sw.ts`, manifest, precache/push boundary per [`clients/lumogis-web/src/pwa/README.md`](../../clients/lumogis-web/src/pwa/README.md)); **Phase 4** Web Push MVP — [cross-device-web-phase-4-web-push-plan.md](cross-device-web-phase-4-web-push-plan.md) (**FP-053** `ACTION_EXECUTED`→push still deferred); **Phase 5** Capture / QuickCapture MVP — [cross-device-web-phase-5-capture-plan.md](cross-device-web-phase-5-capture-plan.md). **Phase 6** (Tauri) remains a **stub**.
- **Still open / follow-ups (non-exhaustive):** **batch-job depth in admin diagnostics** (LWAS-4 / `FP-012` area), **legacy admin SPA replacement** (deferred), **optional web e2e in CI** (`FP-047`), capture indexed content vs **semantic_search** on **`documents`** only (**FP-TBD-5.1**-class gap — see [`product-roadmap-reconciliation-audit-2026-05-02.md`](product-roadmap-reconciliation-audit-2026-05-02.md)), plus capability productisation (**FP-048+**). **Admin user import/export UI** and **password management foundation** remain as shipped.

**Recommended next product chunk:** Do **not** treat **Phase 2 mobile UX** as the default “next” slice — it has a closed extraction doc. Use **`product-roadmap-reconciliation-audit-2026-05-02.md`** (or current portfolio) to pick the next headline gap (e.g. capture ↔ memory search parity, **FP-053**, runtime credential resolution).

**Post-capture strategic baseline:** [Agentic Core](agentic_core.md) is documented as the next major architecture/product direction **after** voice/capture is complete. Its first two post-capture slices are a code-defined static agent registry + `EffectiveAgentPolicy` model, then a read-only Lumogis Web AI Team page; no agent runtime, writes, cloud escalation, or capability-provided agents are part of that first slice.

- **Agent Harness Foundation (architecture theme, not a separate programme):** [`docs/decisions/034-agent-harness-foundation-terminology-and-boundaries.md`](../decisions/034-agent-harness-foundation-terminology-and-boundaries.md) names the **existing** Core primitives (bounded LLM loop, `ToolSpec` / read-only ToolCatalog, Ask/Do, actions+audit, capabilities, MCP as **external** interoperability, hooks, diagnostics) and defers SessionTimeline until privacy/product agreement. It **does not** add a second backlog: execution stays **Linear** (`AGENTS.md`). Related portfolio rows (non-exclusive) include **FP-019** (MCP), **FP-016** (connector permissions), **FP-048–FP-051** (capability/OOP follow-ups), plus existing admin diagnostics / tool-vocabulary / Ask–Do ADR tracks. New Linear issues should **link** ADR 034 and these parents instead of duplicating them.

---

## 1. Current status by plan

| Artefact | Status | Notes |
| --- | --- | --- |
| **Parent** `cross_device_lumogis_web` | **Phase 0–5 MVP slices closed in-repo** via architecture extractions; **Phase 6** stub. *(Maintainer-local parent plan frontmatter may still lag.)* | Remediation Phase 4/5 ≠ parent Phase 4/5 — still true (see naming collision above). |
| **Child** `lumogis_web_admin_shell` | **Implemented / closed** for product (Me + Admin shells). | Test hardening partially done (`FP-046` closed); **optional CI** still open (`FP-047`). |
| **Remediation** `lumogis-self-hosted-platform-remediation-plan` | Chunks through **Phase 4 household control** + **Phase 5 capability scaffolding** are **sufficiently complete** to pause that stream (per Phase 4/5 closeout reviews). | **Platform** work; orthogonal labels vs **cross-device** Phase 4/5 (see collision note). |

---

## 2. Admin/Me shell reconciliation (`lumogis_web_admin_shell.plan.md`)

### 2.1 Should the child plan “stay closed”?

**Yes — for product delivery.** The shells are routable, navigable, and the listed views exist under `clients/lumogis-web/src/features/me/` and `…/admin/`. `App.tsx` defines nested `/me/*` and `/admin/*` (including `tools-capabilities`, `llm-providers`, `notifications`, `diagnostics`).

**Caveat:** The **written** “Out of scope / Follow-up register” in the child plan is **partly stale** (see §6). Treat the **reconciliation doc + repo** as the up-to-date picture for what remediation closed vs what remains.

### 2.2 Follow-up rows vs current repo

| Slug / row | Child plan (LWAS) intent | Reconciliation |
| --- | --- | --- |
| **LWAS-2** `lumogis_me_llm_providers_view` | Typed `GET /api/v1/me/llm-providers` | **Effectively done** — OpenAPI + `MeLlmProvidersView` + `meLlmProviders.ts` call the façade. *Close/supersede* the “missing typed route” story; any new work is **polish** (edge cases), not “add the route”. |
| **LWAS-3b / notifications façade** (Phase 4 remediation) | Read-only notification **status** | **Done** — `GET /api/v1/me/notifications` + `MeNotificationsView` (read-only; no tokens). This is **not** parent **Web Push** (see §3). |
| **LWAS-3d / admin diagnostics** (remediation) | `GET /api/v1/admin/diagnostics` + summary | **Done** — extends beyond **credential-key fingerprint** only; fingerprint remains a **separate** `GET` used by the same view. |
| **me tools / capabilities** | `GET /api/v1/me/tools` + Web table | **Done** — `/me/tools-capabilities`, `MeToolsCapabilitiesView`, documented in `tool-vocabulary.md` and `README.md`. |
| **LWAS-1** `lumogis_me_password` (superseded by **`lumogis_password_management_foundation`**) | Self-service `POST /api/v1/me/password` + UI; admin `POST /api/v1/admin/users/{id}/password`; CLI `python -m scripts.reset_password` (cwd **`orchestrator/`**) | **Done in repo** — `MeProfileView` change-password; **Admin → Users** reset; refresh JTI cleared on change/reset; **no** SMTP / email forgot-password (defer `lumogis_forgot_password_email_reset`). |
| **Admin household password reset** (related) | Admin sets another user’s password when someone forgets | **Done** — same chunk as LWAS-1 supersession above. |
| **LWAS-3** `lumogis_web_admin_user_import_export` | Admin import/export UI | **Done in repo** — `AdminUsersView`: backup inventory + dry-run/real import + per-row export (ZIP on server path + admin export API). |
| **LWAS-4** `lumogis_web_admin_diagnostics_batch_jobs` | Queue depth in diagnostics | **Still open** — depends on batch-job **diagnostic** surfacing in Core. |
| **LWAS-7** Web Push **live** under `/me/notifications` | Client subscribe + SW + Phase 4 parent scope | **MVP done** — `clients/lumogis-web/src/features/me/PushOptIn.tsx` + service worker push/`notificationclick` per Phase **4** extraction; **`ACTION_EXECUTED`→push** still **FP-053**. `/me/notifications` combines read-only façade + push enrolment (not interchangeable concepts, but both exist). |
| **Legacy admin SPA replacement** | Link-out only | **Still deferred** — unchanged. |
| **Test / e2e / CI** | Hardening | **Partially** done (`FP-046`); **CI** for e2e **open** (`FP-047`). |

### 2.3 Stale text in the child plan (for readers)

- **“Typed GET /me/llm-providers does not exist”** (§Out of scope) — **stale**; the façade exists post-remediation.
- **“/me/notifications v1 ships ntfy only + Web Push placeholder”** — **stale**: the view combines **`GET /api/v1/me/notifications`** with **`PushOptIn`** (Phase **4C**) per `MeNotificationsView.tsx`.
- **“Admin only redirect to `/` with toast”** in closure record — may be **stale** if redirect was changed to `/chat` for toast visibility; check `AdminPage.tsx` if updating the plan.
- **“No new server endpoints”** — **stale** as a **global** statement: remediation **added** server GET façades; the child plan was *originally* client-only.

**Recommendation:** Do **not** hand-edit the closed plan for small deltas unless a maintainer wants noise; use **this reconciliation doc** + a future **verify-plan** on the child plan if you need the child file’s executive summary to match reality.

---

## 3. Cross-device parent plan reconciliation (`cross_device_lumogis_web.plan.md`)

*The numbered prompts below were written **2026-04-26**. Repository reality advanced the same month; **authoritative closeouts** are the Phase **2–5** extraction markdown files linked from the executive summary.*

### 3.1 Is Phase 2 still open?

**Closed as MVP** — [cross-device-web-phase-2-mobile-ux-plan.md](cross-device-web-phase-2-mobile-ux-plan.md) (`status: implemented`). Further UX polish may still land, but the **2A–2D** extraction is complete.

### 3.2 Is Phase 3 (PWA / bounded caching) still open?

**Partially shipped.** `clients/lumogis-web/src/pwa/` exists (**`sw.ts`**, **`manifest`**, precache + push boundary — [`clients/lumogis-web/src/pwa/README.md`](../../clients/lumogis-web/src/pwa/README.md)). **Full offline Lumogis** and broader install/PWA scope remain intentionally bounded (see README + ADR **030** history).

### 3.3 Is parent Phase 4 (Web Push / background approvals) still open?

**MVP closed** per [cross-device-web-phase-4-web-push-plan.md](cross-device-web-phase-4-web-push-plan.md) (**4A–4E**): server **`/api/v1/notifications/*`**, client **`PushOptIn`**, production **`push` / `notificationclick`** handling. **Still open / deferred:** **`ACTION_EXECUTED` → push** (**FP-053**) and any richer approval-notification templates called out in that extraction.

### 3.4 Is parent Phase 5 (capture) still open?

**MVP closed** per [cross-device-web-phase-5-capture-plan.md](cross-device-web-phase-5-capture-plan.md): **`captures`** API (non-stub product paths), **`QuickCapturePage`** at **`/capture`**, outbox / indexing behaviour as documented there. **Known follow-up:** indexed captures live on Qdrant **`conversations`** while **`semantic_search`** still targets **`documents`** only — **FP-TBD-5.1** class gap (see [`product-roadmap-reconciliation-audit-2026-05-02.md`](product-roadmap-reconciliation-audit-2026-05-02.md)).

### 3.5 Is Phase 6 (Tauri) still a stub?

**Yes** — desktop shell remains **stub / deferred** until explicitly rescoped.

### 3.6 Did remediation “accidentally” complete any parent phase?

| Parent phase | Accidentally complete? | What changed instead |
| --- | --- | --- |
| Phase 0–1 | Already complete before this reconciliation | N/A |
| Phase 2 | **No** — shipped via **cross-device** extraction work | Remediation did not substitute Phase 2 DoD |
| Phase 3 PWA | **No** — shipped via Lumogis Web **`src/pwa/`** programme | Remediation ≠ Phase 3 |
| Phase 3.5 Admin/Me | **Child plan** complete (separate) | — |
| Phase 4 (parent: Push + notifications product) | **No** — shipped via Phase **4** extraction + notifications routes | Remediation added **read-only** façades **in addition** |
| Phase 5 (parent: capture) | **No** — shipped via **`captures`** + QuickCapture programme | Remediation Phase 5 = **capability** scaffolding (different label) |
| Phase 6 | **No** | — |

### 3.7 Stale text in the parent plan

- **Phase 3.5 / split-out** sections that still say “`/me/notifications` ntfy only / Web Push placeholder” — **stale**; combine façade + **`PushOptIn`** (§2.3).
- **Caddy / route** tables in the plan body may lag **incremental** edge routing changes; treat **`docker/caddy/Caddyfile`** and **`vite.config.ts`** as source of truth.
- **ADR:** **`docs/decisions/030-cross-device-client-architecture.md`** is **finalised** for Phase **4** per the Phase **4** extraction — older “draft until Phase 4” lines in unmigrated plan prose may lag.

---

## 4. Follow-up portfolio (`.cursor/follow-up-portfolio.md`) — read-only analysis

**No file edits in this pass** (per task instructions).

| FP / theme | Suggested treatment |
| --- | --- |
| **FP-001** | Remains the umbrella for **parent** cross_device work. **Portfolio note drift:** Phases **2–5** now have **closed MVP extractions** in-repo; **Phase 6** stub + **FP-053** / search-parity gaps remain — reconcile **Notes** on the next verify pass so FP-001 does not imply “all parent phases still open”. |
| **FP-047** | Optional **CI** for `web-e2e` / prove — still valid; not closed by reconciliation. |
| **FP-048 — FP-051** | **Phase 5 capability** follow-ups (invoke URL, KG bearer posture, policy guard, richer `/me/tools` copy) — **open**; orthogonal to parent Phase 2 unless prioritised. |
| **FP-046** | **Done** — historical; do not touch. |
| **Rows not superseded** | **FP-002** (open questions), **FP-006/007** (auth session / rate limit infra), other Core topics — still valid; **not** “closed by” Web roadmap reconciliation alone. |

**Proposed action:** When the next chunk ships, whoever runs **`/verify-plan`** or **`/record-retro`** should **merge** a short **FP-001 Notes** update — not manual portfolio edits in this document pass.

---

## 5. Current Web backlog (grouped)

**Superseded table — 2026-04-26 snapshot.** The rows below claimed Phases **2–5** were **open** and that **`src/pwa/`**, **`PushOptIn`**, and **`QuickCapture`** were absent; that is **false at current HEAD**. Use **[`product-roadmap-reconciliation-audit-2026-05-02.md`](product-roadmap-reconciliation-audit-2026-05-02.md)** for a backlog-style table; **LWAS-4**, **`FP-047`**, **`FP-048+`**, **`FP-053`**, and **capture ↔ memory search** parity remain common open themes.

---

## 6. Recommended next implementation chunk (exactly one)

**Superseded — 2026-04-26.** Phase **2** extraction is **closed**; do not treat **`cross_device_web_phase_2_mobile_ux_hardening`** as the default next chunk without re-reading **`product-roadmap-reconciliation-audit-2026-05-02.md`** / portfolio. **`lumogis_password_management_foundation`** and admin import/export remain shipped.

---

## 7. Risks and documentation hygiene

- **Phase number ambiguity** — Always disambiguate **“remediation Phase N”** vs **“cross_device Phase N”** in PRs and plans.
- **Double-counting FP-001** — Remediation advanced **platform** slices; **cross-device** Phases **2–5 MVP** also shipped via **separate** extraction work — portfolio wording should reflect **Phase 6** + **follow-up FPs**, not “Phases 2–6 all open”.
- **Stale child/parent paragraphs** — Prefer **`product-roadmap-reconciliation-audit-2026-05-02.md`**, **Phase 2–5 extraction docs**, and **closeout reviews** over unmigrated maintainer-local plan prose.
- **Web Push layers** — **`GET /api/v1/me/notifications`** (read façade), **`PushOptIn`** + **`/api/v1/notifications/*`** (browser push enrolment), service worker **`push`/`notificationclick`** (Phase **4** extraction), and deferred **`ACTION_EXECUTED`→push** (**FP-053**) are **related but not interchangeable**.

---

## 8. Verification (discovery) notes

Original **2026-04-26** discovery bullets are **incorrect for current HEAD**. **2026-05-03 spot-check:** `clients/lumogis-web/src/pwa/` (**`sw.ts`**, manifest, push helpers), **`clients/lumogis-web/src/features/me/PushOptIn.tsx`**, **`clients/lumogis-web/src/features/capture/QuickCapturePage.tsx`**, route **`/capture`** in `App.tsx`, and implementation **`orchestrator/routes/api_v1/captures.py`** (product paths beyond **`501`** stubs). OpenAPI snapshot still carries the Me/Admin façade paths listed above; **`notifications.py`** remains the Web Push server surface.

---

## Related

- [`cross-device-web-phase-2-mobile-ux-plan.md`](./cross-device-web-phase-2-mobile-ux-plan.md) — **Phase 2** — **implemented** extraction (**2A–2D**).
- [`cross-device-web-phase-4-web-push-plan.md`](./cross-device-web-phase-4-web-push-plan.md) — **Phase 4** Web Push (**4A–4E** closed; **FP-053** deferred).
- [`cross-device-web-phase-5-capture-plan.md`](./cross-device-web-phase-5-capture-plan.md) — **Phase 5** Capture / QuickCapture MVP.
- [`product-roadmap-reconciliation-audit-2026-05-02.md`](./product-roadmap-reconciliation-audit-2026-05-02.md) — backlog-oriented reconciliation vs extraction closeouts.
- [`lumogis-self-hosted-platform-remediation-plan.md`](./lumogis-self-hosted-platform-remediation-plan.md) — **pointer** added (see that file) to this document.
- [`phase-4-household-control-surface-closeout-review.md`](./phase-4-household-control-surface-closeout-review.md) — **Phase 4 (household)** matrix.
- [`phase-5-final-capability-scaffolding-closeout-review.md`](./phase-5-final-capability-scaffolding-closeout-review.md) — **Phase 5 (capability)** matrix.
- [`tool-vocabulary.md`](./tool-vocabulary.md) — tools vs `GET /api/v1/me/tools` vs execution.
