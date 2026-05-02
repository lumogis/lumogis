# Web roadmap reconciliation — after self-hosted architecture remediation

**Slug:** `lumogis_web_roadmap_reconciliation_after_remediation`  
**Date:** 2026-04-26  
**Kind:** Planning / reconciliation doc; product updates may be noted here when they close tracked gaps (e.g. password management foundation, 2026-04).

**Sources:** `cross_device_lumogis_web.plan.md` (parent), `lumogis_web_admin_shell.plan.md` (child), `lumogis-self-hosted-platform-remediation-plan.md`, Phase 4/5 closeout reviews, `tool-vocabulary.md`, `clients/lumogis-web/README.md`, `App.tsx`, `openapi.snapshot.json`, `.cursor/follow-up-portfolio.md` (read-only for this document).

**Naming collision (read this first):** The **self-hosted remediation programme** uses “Phase 4 / Phase 5” for *household-control surfaces* and *capability scaffolding* (see remediation plan §3). The **parent** `cross_device_lumogis_web` plan uses “Phase 4 / Phase 5” for *Web Push + approval notifications* and *capture-from-anywhere*. Those are **different programmes**; completion of remediation Phase 4/5 does **not** mark the parent’s Phase 4/5 complete.

---

## Executive summary

- **Done:** Parent **Phase 0** (v1 façade + OpenAPI) and **Phase 1** (Lumogis Web core + Caddy same-origin) remain the verified baseline. **Child plan** `lumogis_web_admin_shell` remains **product-complete** for Me/Admin shells; optional test/CI gaps (e.g. CI for Playwright) are **non-blocking**.
- **Superseded or materially advanced by remediation:** Remediation **Chunk 6 / Phase 4 (household control)** delivered typed `GET` façades and Web views: `/api/v1/me/tools` → `/me/tools-capabilities`, `/api/v1/me/llm-providers` → `/me/llm-providers`, `/api/v1/me/notifications` → `/me/notifications`, `/api/v1/admin/diagnostics` → `/admin/diagnostics`. This **closes the intent** of several **child-plan follow-up rows** that assumed those surfaces did not exist (e.g. `lumogis_me_llm_providers_view` as a separate typed route — it now exists; notifications are **not** “ntfy-only placeholder” as a whole — the page is a **read-only channel/status façade** backed by the new API). **Remediation Phase 5** (capability scaffolding, mock capability, OOP audit, permission-labelled catalog) is **complete** as a *platform* slice; it does **not** ship parent **“capture from anywhere”** (different Phase 5).
- **Still open (parent):** **Phases 2–6** of `cross_device_lumogis_web` — mobile UX polish, PWA + bounded caching, full **Web Push client + background approvals** (distinct from the `/me/notifications` **settings** view), real **capture** implementation (replace `501` stubs with product behaviour + `QuickCapture` UI), Tauri stub. **Child-plan** items that remain: **batch-job depth in admin diagnostics** (server dependency), **Web Push opt-in UI** wiring to existing `/api/v1/notifications/*`, **legacy admin SPA replacement** (deferred), **optional web e2e in CI** (`FP-047`). **Admin user import/export UI** (`lumogis_web_admin_user_import_export` / LWAS-3) is **implemented in repo** — **Admin → Users** uses `GET`/`POST /api/v1/admin/user-imports` and per-user **Export backup** via `POST /api/v1/me/export` with `target_user_id`. **Account password management** for self-hosted instances is **addressed** by chunk **`lumogis_password_management_foundation`** (self-service change, admin reset, `python -m scripts.reset_password` from **`orchestrator/`** — **not** email reset; deferred as `lumogis_forgot_password_email_reset`).

**Recommended next product chunk (one):** Resume the **parent** roadmap in order: **`cross_device_web_phase_2_mobile_ux_hardening`** — it is the next numbered phase after the verified Phase 1 closure, unlocks a coherent “mobile companion” story before PWA (Phase 3), and matches `FP-001`’s remaining scope. **Slicing and gap analysis (2026-04-26):** [cross-device-web-phase-2-mobile-ux-plan.md](cross-device-web-phase-2-mobile-ux-plan.md) — first implementation chunk **2A** (Me/Admin sub-layout on narrow viewports) before 2B–2D.

**Post-capture strategic baseline:** [Agentic Core](agentic_core.md) is documented as the next major architecture/product direction **after** voice/capture is complete. Its first two post-capture slices are a code-defined static agent registry + `EffectiveAgentPolicy` model, then a read-only Lumogis Web AI Team page; no agent runtime, writes, cloud escalation, or capability-provided agents are part of that first slice.

---

## 1. Current status by plan

| Artefact | Status | Notes |
| --- | --- | --- |
| **Parent** `cross_device_lumogis_web` | **Phase 0 + Phase 1 implemented and verified; Phases 2–6 open** (per plan frontmatter and executive summary). | Phases 2–5 in the *parent* sense are **not** satisfied by remediation alone (see §3). |
| **Child** `lumogis_web_admin_shell` | **Implemented / closed** for product (Me + Admin shells). | Test hardening partially done (`FP-046` closed); **optional CI** still open (`FP-047`). |
| **Remediation** `lumogis-self-hosted-platform-remediation-plan` | Chunks through **Phase 4 household control** + **Phase 5 capability scaffolding** are **sufficiently complete** to pause that stream (per Phase 4/5 closeout reviews). | This is **platform/architecture** work, not a substitute for the parent’s Phases 2–6. |

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
| **LWAS-7** Web Push **live** under `/me/notifications` | Client subscribe + SW + Phase 4 parent scope | **Still open** — there is **no** `PushOptIn.tsx` in the tree; server routes under `/api/v1/notifications` exist, but full **parent Phase 4** (push UX + background approvals) is **not** done. The current `/me/notifications` page is **settings/status**, not a substitute for “Pushopt-in / SW” in the parent plan. |
| **Legacy admin SPA replacement** | Link-out only | **Still deferred** — unchanged. |
| **Test / e2e / CI** | Hardening | **Partially** done (`FP-046`); **CI** for e2e **open** (`FP-047`). |

### 2.3 Stale text in the child plan (for readers)

- **“Typed GET /me/llm-providers does not exist”** (§Out of scope) — **stale**; the façade exists post-remediation.
- **“/me/notifications v1 ships ntfy only + Web Push placeholder”** — **partially stale**: the main view is now the **read-only** `GET /api/v1/me/notifications` façade; **separate** Web Push **opt-in UI** (parent Phase 4) can still be absent or minimal — verify against `MeNotificationsView` (no tabbed “Push” flow in the current file; no `PushOptIn` component in repo).
- **“Admin only redirect to `/` with toast”** in closure record — may be **stale** if redirect was changed to `/chat` for toast visibility; check `AdminPage.tsx` if updating the plan.
- **“No new server endpoints”** — **stale** as a **global** statement: remediation **added** server GET façades; the child plan was *originally* client-only.

**Recommendation:** Do **not** hand-edit the closed plan for small deltas unless a maintainer wants noise; use **this reconciliation doc** + a future **verify-plan** on the child plan if you need the child file’s executive summary to match reality.

---

## 3. Cross-device parent plan reconciliation (`cross_device_lumogis_web.plan.md`)

### 3.1 Is Phase 2 still open?

**Yes.** The parent plan’s **Phase 2 — Mobile companion UX** (responsive polish, performance budgets, etc.) is **not** supplanted by architecture remediation. Remediation did not deliver the parent’s Phase 2 DoD.

### 3.2 Is Phase 3 (PWA / bounded caching) still open?

**Yes.** There is **no** `src/pwa/` tree (no `sw.ts` / `manifest` as listed in the parent “New files” — verified by glob). `package.json` still describes a “responsive PWA client” as **aspirational** for Phases 1–5 per the plan; service worker + installability are **not** done.

### 3.3 Is parent Phase 4 (Web Push / background approvals) still open?

**Yes, substantially.** Evidence:

- **Server:** `orchestrator/routes/api_v1/notifications.py` exists (subscribe, VAPID, etc.) — part of the **v1 façade** and parent Phase 4 **server** work may be **partially** present.
- **Client / parent DoD:** No `PushOptIn` component; parent Phase 4’s **end-to-end opt-in** + **Service Worker** + **background approvals** are **not** the same as **`/me/notifications`**, which is a **read-only settings/status** page over `GET /api/v1/me/notifications` (remediation **household** surface).

**Do not** mark parent Phase 4 complete based on `/me/notifications` alone.

### 3.4 Is parent Phase 5 (capture) still open?

**Yes.** `orchestrator/routes/api_v1/captures.py` remains a **stub** path (product behaviour is not the parent’s “queued capture” story until implemented). There is **no** `QuickCapture` UI. **Remediation Phase 5** = **capability** scaffolding; **parent Phase 5** = **capture uploads** — different work.

### 3.5 Is Phase 6 (Tauri) still a stub?

**Yes** — per parent plan, Phase 6 is a **checklist / stub**; no requirement to change that.

### 3.6 Did remediation “accidentally” complete any parent phase?

| Parent phase | Accidentally complete? | What changed instead |
| --- | --- | --- |
| Phase 0–1 | Already complete before this reconciliation | N/A |
| Phase 2 | **No** | — |
| Phase 3 PWA | **No** | — |
| Phase 3.5 Admin/Me | **Child plan** complete (separate) | — |
| Phase 4 (parent: Push + notifications product) | **No** (client SW / push opt-in not done) | **Remediation Phase 4** added **read-only** façades (`/api/v1/me/notifications`, etc.) — different scope |
| Phase 5 (parent: capture) | **No** | **Remediation Phase 5** = capability / execution / catalog hardening — different scope |
| Phase 6 | **No** | — |

### 3.7 Stale text in the parent plan

- **Phase 3.5 / split-out** sections that still say “`/me/notifications` ntfy only / Web Push placeholder” — **stale** relative to the **read-only** façade (see §2).
- **Caddy / route** tables in the plan body may lag **incremental** edge routing changes; treat **`docker/caddy/Caddyfile`** and **`vite.config.ts`** as source of truth.
- **ADR** line “finalise after Phase 4” — policy question for maintainers: parent **Phase 4** (push) is not done, but **remediation** delivered large client-facing surfaces; ADR finalisation may need a **separate** decision (out of scope here).

---

## 4. Follow-up portfolio (`.cursor/follow-up-portfolio.md`) — read-only analysis

**No file edits in this pass** (per task instructions).

| FP / theme | Suggested treatment |
| --- | --- |
| **FP-001** | Remains the umbrella for **parent** cross_device work. **Add evidence in a future verify pass** (not here): Phases 2–6 still open; remediation completed **separate** Phase 4/5 (household + capability) — clarify in **Notes** to avoid “double-counting” platform work as product roadmap. |
| **FP-047** | Optional **CI** for `web-e2e` / prove — still valid; not closed by reconciliation. |
| **FP-048 — FP-051** | **Phase 5 capability** follow-ups (invoke URL, KG bearer posture, policy guard, richer `/me/tools` copy) — **open**; orthogonal to parent Phase 2 unless prioritised. |
| **FP-046** | **Done** — historical; do not touch. |
| **Rows not superseded** | **FP-002** (open questions), **FP-006/007** (auth session / rate limit infra), other Core topics — still valid; **not** “closed by” Web roadmap reconciliation alone. |

**Proposed action:** When the next chunk ships, whoever runs **`/verify-plan`** or **`/record-retro`** should **merge** a short **FP-001 Notes** update — not manual portfolio edits in this document pass.

---

## 5. Current Web backlog (grouped)

### A. Immediate product gaps (highest end-user or operator impact)

| Item | Source | Status | Why open | Suggested priority |
| --- | --- | --- | --- | --- |
| **Password management** `lumogis_password_management_foundation` (incl. former `lumogis_me_password`) | LWAS-1 + admin reset + CLI | **Closed in repo** | `POST /api/v1/me/password`, admin reset, `scripts.reset_password`; email forgot-password **deferred** (`lumogis_forgot_password_email_reset`) | Was **High** — now track only deferred email flow if desired |
| **Admin user import/export UI** | Child LWAS-3 | **Closed in repo** | Shipped in **Admin → Users** (`user-imports` + `me/export` admin path) | Was **Medium–High** — closed 2026-04 |
| **Parent Phase 2** mobile companion UX | `cross_device` §Phase 2 | **Open** | Not started | **High** (roadmap order) |
| **Parent Phase 4** Web Push + approvals UX (full) | `cross_device` §Phase 4 | **Open** | SW + opt-in + product flows; `/me/notifications` is **not** a substitute | **High** (when TLS/PWA path is clear) — **not** confounded with remediation façade |
| **Parent Phase 5** capture (replace stubs) | `cross_device` §Phase 5 | **Open** | Stubs + no `QuickCapture` | **Medium** (product differentiator) |

### B. Cross-device / mobile roadmap

| Item | Source | Status | Notes |
| --- | --- | --- | --- |
| **Parent Phase 3** PWA + bounded caching | `cross_device` | **Open** | No `src/pwa/` in repo |
| **Service worker / installability** | Parent Phase 3 | **Open** | |
| **IndexedDB drafts** (parent) | Parent Phase 3 | **Open** | |

### C. Admin / ops backlog

| Item | Source | Status | Notes |
| --- | --- | --- | --- |
| **Admin diagnostics: batch / queue depth** | Child LWAS-4, `FP-012` area | **Open** | Needs Core diagnostic surfacing |
| **Legacy admin SPA replacement** | Child plan | **Deferred** | Link-out still valid |
| **Web e2e in CI** | `FP-047` | **Open** | |
| **Richer `/me/tools` unavailable reasons** | `FP-051` | **Open** | Hardening |

### D. Deferred / future

| Item | Source | Status |
| --- | --- | --- |
| **Tauri / Phase 6** | `cross_device` | **Stub / deferred** |
| **Forgot-password email / SMTP reset** | `lumogis_forgot_password_email_reset` | **Deferred** (requires outbound email product decision) |
| **Host marketplace / multi-tenant** | Portfolio `FP-004` / `FP-045` | **Deferred** (explicitly out of near-term) |
| **Capability productisation** (`FP-048`+) | Phase 5 closeout | **Open** (platform) |

---

## 6. Recommended next implementation chunk (exactly one)

**Primary recommendation:** **`cross_device_web_phase_2_mobile_ux_hardening`**

- **Rationale:** Parent plan **Phases 2–6** are explicitly still open; **Phase 2** is the **next in-order** slice after the verified Phase 1 programme. It restores momentum on the **Web product** roadmap after a long **platform/remediation** arc, and it sets up **Phase 3 PWA** coherently (mobile UX first, then installability + caching). It aligns with **FP-001**’s remaining scope.
- **Concrete scope (derive details from parent §Phase 2 in-repo):** responsive layouts, touch targets, navigation on small viewports, performance budgets / Lighthouse as the parent plan specifies — **without** re-scoping to remediation items.

**~~Alternative~~ (shipped):** **`lumogis_password_management_foundation`** replaced the narrow `lumogis_me_password` follow-up (self-service + admin + shell recovery).

**Not recommended as the “next” chunk** unless product explicitly prioritises ops: `web_e2e_ci_hardening` (FP-047 — important but **infra**, not the first product slice after roadmap return). *(Former LWAS-3 admin import/export UI is now in-repo.)*

---

## 7. Risks and documentation hygiene

- **Phase number ambiguity** — Always disambiguate **“remediation Phase N”** vs **“cross_device Phase N”** in PRs and plans.
- **Double-counting FP-001** — Remediation work advanced the **platform**; the **Web roadmap** still owns Phases 2–6 of the **parent** plan.
- **Stale child/parent paragraphs** — Prefer **this reconciliation file** and **closeout reviews** over scattered edits to **closed** plan files.
- **Web Push confusion** — Three different ideas: (1) **`/api/v1/me/notifications`** read-only façade, (2) **`/api/v1/notifications/*`** subscription API, (3) **parent** “background approvals + SW” — all three are distinct layers.

---

## 8. Verification (discovery) notes

The following were used while authoring this document:

- **Routes in client:** `rg` on `clients/lumogis-web/src` for `/me/tools-capabilities`, `/me/llm-providers`, `/me/notifications`, `/admin/diagnostics` — all present in nav and/or API helpers.
- **OpenAPI snapshot:** paths `/api/v1/me/tools`, `/api/v1/me/llm-providers`, `/api/v1/me/notifications`, `/api/v1/me/password`, `/api/v1/me/export`, `/api/v1/admin/diagnostics`, `/api/v1/admin/users/{user_id}/password`, `/api/v1/admin/user-imports` present in `clients/lumogis-web/openapi.snapshot.json`.
- **Missing parent-plan client files:** no `src/pwa/`, no `PushOptIn.tsx`, no `QuickCapture.tsx` (glob under `clients/lumogis-web/src`).
- **Web Push server:** `orchestrator/routes/api_v1/notifications.py` exists (VAPID-gated routes).

*(Optional local checks, if running in a dev environment: `npm test`, `npm run lint`, `npm run build` in `clients/lumogis-web` — not required for the validity of this planning document.)*

---

## Related

- [`cross-device-web-phase-2-mobile-ux-plan.md`](./cross-device-web-phase-2-mobile-ux-plan.md) — **Phase 2** (parent) scoped into **2A–2D**; **2A** = Me/Admin responsive sub-shells.
- [`lumogis-self-hosted-platform-remediation-plan.md`](./lumogis-self-hosted-platform-remediation-plan.md) — **pointer** added (see that file) to this document.
- [`phase-4-household-control-surface-closeout-review.md`](./phase-4-household-control-surface-closeout-review.md) — **Phase 4 (household)** matrix.
- [`phase-5-final-capability-scaffolding-closeout-review.md`](./phase-5-final-capability-scaffolding-closeout-review.md) — **Phase 5 (capability)** matrix.
- [`tool-vocabulary.md`](./tool-vocabulary.md) — tools vs `GET /api/v1/me/tools` vs execution.
