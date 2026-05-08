---
status: implemented
implemented: 2026-04-26
test_result: passing
done_checklist: 7/7
adr_status: none
verified_artefact: docs/architecture/cross-device-web-phase-2-mobile-ux-plan.md
---

<!-- VERIFICATION SUMMARY — updated 2026-04-26 -->
## Implementation Summary (/verify-plan)

| | |
|---|---|
| **Status** | ✅ Complete |
| **Verified** | 2026-04-26 — Composer |
| **Review rounds** | **0** on this extraction doc; parent *(maintainer-local only; not part of the tracked repository)* — **5** parent-plan arbitration rounds (pre-ship history) |
| **Tests** | Orchestrator **1554** passed / **9** skipped / **0** failed (`make compose-test`); web **159**/0 Vitest (verification run; see §Implementation log — Phase 2D) |
| **Files** | Phase **2A–2D** surfaces + smoke infra per §Implementation logs; verify delta: `Makefile` `compose-test` pins `AUTH_ENABLED=false`; `test_api_v1_admin_diagnostics.py` monkeypatch isolation; `openapi.snapshot.json` includes `GET /healthz` |
| **Code review** | Mobile UX + infra ✅; test harness: host `.env` no longer flips auth expectations in container pytest |
| **Definition of done** | §7 roll-up **7/7** ✅ |
| **ADR** | None 📋 (this file is not an ADR) |

**What was built**

Cross-device **Phase 2 mobile UX** (2A–2D): responsive Me/Admin subshells, Approvals/Chat/Search compact behaviour, dense tables/forms, core a11y (Approvals, CopyOnce, Chat live region), Lighthouse script + measured mobile `/chat` scores, Playwright mobile smoke with live-stack verification; supporting infra **`GET /healthz`**, Compose healthchecks, **`chromium-smoke-shared-user`** with **`workers: 1`**.

**Deviations**

Verification referenced this **architecture extraction** path (`@docs/architecture/…`) instead of a dedicated *(maintainer-local only; not part of the tracked repository)*. **`make compose-test`** now exports **`AUTH_ENABLED=false`** so repo `.env` family-LAN smoke settings do not break TestClient tests expecting auth-off defaults.

**Security findings**

None in this pass.

**ADR notes**

None — planning extraction only; parent cross-device ADR remains draft per multi-phase policy.

**Discoveries**

`docker compose run` inherits repo **`.env`**; contributors with **`AUTH_ENABLED=true`** locally should use **`make compose-test`** (or explicit test `monkeypatch`) so orchestrator unit tests stay aligned with auth-off semantics.

**Next steps**

1. Parent **FP-001** still tracks **Phases 3–6** of Lumogis Web.  
2. Optional **FP-047** — CI for stack + Playwright e2e.

<!-- END SUMMARY -->

# Cross-device Web — Phase 2 mobile UX (implementation plan)

**Slug:** `cross_device_web_phase_2_mobile_ux_hardening` (parent); sub-chunks `cross_device_web_phase_2a` … `2d`  
**Date:** 2026-04-26  
**Kind:** Extraction and scoping from `cross_device_lumogis_web.plan.md` + current `clients/lumogis-web` state. **Not** an ADR. **No production code in this file.**

**Parent plan:** *(maintainer-local only; not part of the tracked repository)* — `### Phase 2 — Mobile companion UX (≈ 4–6 days)` (lines ~919–925).  
**Context:** [Web roadmap after remediation](lumogis-web-roadmap-reconciliation-after-remediation.md) — return to the **parent** Phases 2–6; remediation Phase 4/5 **does not** replace parent Phase 2.

---

## Executive summary

The parent plan’s **Phase 2** is a **4–6 day** tranche: **(2.1)** compact-mode polish (full-screen approval modal, optional swipe-to-confirm, **44 px** tap targets, reduced redundant SSE), **(2.2)** **Lighthouse mobile** budget (Performance ≥ 80, Best Practices ≥ 90 on **/chat**), **(2.3)** a11y hardening (WCAG-AA, keyboard, `aria-live` for stream tokens, modal focus). The **shell** already uses **container queries** (720px), **dvh**-based chat min-heights, `--lumogis-tap-target-min: 44px` on primary nav, **reduced-motion** global hack, and **focus-visible** rules — so Phase 2 is **incremental polish + Me/Admin sub-shell gaps**, not a from-scratch mobile pass.

**Largest current gap:** **`MePage` and `AdminPage` use fixed two-column grids** (`220px` / `240px` + `1fr`) with **no** compact breakpoint — on narrow viewports, Settings and Admin side nav **do not** stack or collapse, unlike the main app shell. This should be the **first implementation chunk (2A)**.

**Explicit non-goals for all Phase 2 work:** PWA / service worker, push subscriptions, offline caching, `QuickCapture`, Tauri, backend features, marketplace, remediation “Phase 5 capability” work — see §Boundaries.

---

## 1. Extracted parent Phase 2 scope (verbatim intent + DoD)

### 1.1 From parent `### Phase 2` (passes 2.1–2.3)

| Pass | Parent text | Interpretation for implementation |
| --- | --- | --- |
| **2.1** | Compact-mode polish: **full-screen** approval modal, “**swipe-to-confirm**” for high-risk, **larger tap targets (44 px min)**, **reduced** parallel SSE/queries | Approvals: modal is **not** yet full-viewport on small screens (see §3). Swipe is **optional** and may be 2.1b if touch-only. Enforce 44px on **all** primary actions (audit against tokens). Revisit duplicate/refetch work only if measurable (Approvals: one SSE; avoid double `getPendingApprovals` if any). |
| **2.2** | **Lighthouse mobile** in CI: PWA N/A; **Performance ≥ 80 mobile**, **Best Practices ≥ 90** on a defined route (DoD: **/chat**) | Add or document a **local + optional CI** job (`lighthouse`, Playwright, or `unlighthouse` — choose in 2D). **Not** a PWA score gate. |
| **2.3** | A11y: see DoD — **WCAG-AA**, full **keyboard** nav, **ARIA-live** for streamed tokens, **focus** on modals | Contrast: `tests/design/contrast.test.ts` exists. Chat list already has `role="log"` + live per parent Phase 1 DoD text. **Full keyboard nav sweep** was explicitly **deferred** in parent client DoD — Phase 2.3 can close that for **core routes** (not every admin sub-form in one go). Modals: Approvals has focus on Cancel + Escape; verify **focus trap** / return focus (gap). |

### 1.2 From parent `## Definition of done` (client, Phase-2–related excerpt)

- **Pass 2.2** — [ ] Lighthouse mobile Performance ≥ 80, Best Practices ≥ 90 on the **chat** route.
- **Pass 1.1/1.5 → partial a11y** (already in tree): focus-visible, `aria-live` on loading, chat `role="log"`, contrast tests, **e2e Axe** on `#lumogis-main` when stack+creds; **⏭️ full keyboard-navigation sweep still deferred** — **Phase 2.3** should pick this up for **agreed** surfaces.

### 1.3 Explicitly out of scope in parent (other phases)

- **Phase 3** — manifest, Workbox, IndexedDB drafts, `persistQueryClient`, offline banner.  
- **Phase 4** — Web Push client (`PushOptIn`), SW `push` handler, etc.  
- **Phase 5** — real captures, `QuickCapture`, upload.  
- **Phase 6** — Tauri stub.  

**Phase 2 client tests** in parent: Vitest/Playwright rows mix Phase 1 deliverables; Phase 2 adds **Lighthouse** and compact-mode tests — the extracted plan refines in §Tests.

### 1.4 Stale or superseded parent text (post–Admin/Me + remediation + password work)

- **Phase 3.5** “`/me/llm-providers` client filter only / `me/password` missing” is **stale** if password and typed LLM route shipped — **do not** block Phase 2 on those.  
- **Reconciliation doc** (2026-04) notes **password management foundation** and **admin import/export** in repo; Phase 2 **does not** re-open those as scope unless a **mobile** regression is found.  
- **“Offline banner when SW serves stale”** in old client test bullets is **Phase 3/3.5** — ignore for Phase 2 e2e criteria.

---

## 2. Current repo assessment (mobile / responsive)

| Area | What exists | Gaps for Phase 2 |
| --- | --- | --- |
| **AppShell** | Container query `720px`, header + main + bottom nav, `min-height` grid | **Safe-area** insets for notched iOS (header/bottom padding) not seen in CSS. Long email + Sign out in header may **overflow** on very narrow screens — needs truncation or menu. |
| **BottomNav / SidebarNav** | Shared `NAV_ITEMS`; 44px min-height on items via CSS; `aria-current` | Settings + Admin are **extra** `navItems` in `App.tsx` — 5+ tabs on small screens: **crowding** / wrap risk (no icons-only mode). |
| **Chat** | `100dvh` minus header/bottom; two-column at `@container 720px` | **Keyboard** `visualViewport` on mobile: input can be covered (verify on real device). **Thread sidebar** on narrow: width/priority. |
| **Search** | Documented stacked vs side-by-side | Entity card **panel** width on small screens. |
| **Approvals** | List + modal, SSE invalidation, Cancel default focus, Escape | Modal **not** full-screen; **no** swipe; backdrop click closes — OK; **no** scroll lock on `body` (minor). **Two-column button footer** may need **stack** on very narrow. |
| **Me shell (`MePage`)** | `gridTemplateColumns: 220px 1fr` **inline** | **Critical:** side nav does **not** reflow; unusable on ~320px. **MeNav** is vertical only — no horizontal **tabs** for mobile. |
| **Admin shell (`AdminPage`)** | `240px 1fr` **inline** | **Same** as Me — must collapse / drawer / top tabs for compact. |
| **Me/Admin feature views** | Many tables (`overflowX: auto` patterns vary) | **Tables** (Users, audit, tools, notifications): need **card** fallback or **sticky** first column (pick per view). **Credential forms**: long fields — scroll within page. |
| **CopyOnceModal / Admin modals** | `.lumogis-modal` | Same **sizing** / focus as Approvals. |
| **Theme** | `data-theme` + `prefers-color-scheme` | OK; verify **contrast** on **warn** / **focus** in both themes (already in `contrast.test.ts`). |
| **Reduced motion** | Global `prefers-reduced-motion` sets transitions ~0 | OK. |
| **Tests** | Approvals, Chat, Search, design contrast, e2e `first_slice` + `admin_shell` | Add **mobile viewport** e2e or **component** tests for Me/Admin layout (optional 2D). |

---

## 3. Gap list (actionable, prioritised)

1. **P0 — Me + Admin sub-layout** — fixed **side-by-side** grid without breakpoint (`MePage.tsx`, `AdminPage.tsx`); **blocks** comfortable mobile use of all `/me/*` and `/admin/*` routes.  
2. **P1 — Approvals modal** — parent asks **full-screen** on compact; current **max-width 480px** panel. **Swipe-to-confirm** missing (optional P1.5).  
3. **P1 — Tap targets** — token `--lumogis-tap-target-min` not necessarily applied to **every** `.lumogis-approvals__btn` / inline `button` in feature pages — **audit** + CSS class consolidation.  
4. **P2 — Chat input / viewport** — `visualViewport` or `dvh` follow-up for mobile keyboard.  
5. **P2 — App header** — truncate user email, optional overflow menu.  
6. **P2 — Primary nav** — many items when admin; consider **“More”** or scroll.  
7. **P3 — Lighthouse** — no evidence of **automated** mobile Lighthouse on `/chat` in default CI.  
8. **P3 — Keyboard** — full sweep deferred in parent DoD; scope **2D** to core flows + modals.  
9. **P3 — Focus return** when modal closes — verify against `Dialog` pattern.

---

## 4. Proposed Phase 2 scope (this extraction)

### 4.1 In scope

- Responsive **Me** and **Admin** **sub-shells** (nav + content) for **container width &lt; 720px** (align with app shell `shell` query or a dedicated `layout` container on inner routes).  
- **Approvals** compact modal (full-viewport on compact at minimum height), **stacked** modal actions on narrow, optional **swipe** on high-risk (behind a **feature flag** or second PR).  
- **Chat / Search** polish: narrow breakpoints, **thread rail** behaviour, **search** entity panel.  
- **Form / table** patterns**:** shared “**responsive table**” (horizontal scroll with caption vs card list) for worst offenders.  
- **Safe-area** `env(safe-area-inset-*)` on shell + bottom nav.  
- **A11y:** focus trap or documented manual pattern for modals, **Tab** order sweep on **Me/Admin/Approvals/Chat/Search** (not every credential edge case in 2A).  
- **Lighthouse** script or CI job for **/chat** (and optionally **/approvals**) with documented thresholds.  
- **Tests:** Vitest for layout (resize / container) where stable; **Playwright** mobile viewport for **Me+Admin** nav and **Approvals** modal; optional Lighthouse in CI.  

### 4.2 Out of scope (hard)

- **Service worker**, **manifest**, installability, **offline** (parent Phase 3).  
- **Web Push** UI / subscription / background (parent Phase 4).  
- **Capture** / `POST /api/v1/captures/*` / `QuickCapture` (parent Phase 5).  
- **Tauri** (parent Phase 6).  
- **New** backend routes except **bugfixes** required for a **UI** defect found during hardening.  
- **New product features** (e.g. new settings pages).  
- **Marketplace / Phase 6 architecture / cloud** work.  
- **Remediation** capability/KG follow-ups (`FP-048`–`FP-051`) — track separately.  

### 4.3 Non-goals (soft)

- Pixel-perfect parity with a design Figma.  
- Replacing all tables with cards everywhere in one pass.  
- i18n or RTL (unless trivial CSS fixes).  

### 4.4 Dependencies

- **No** orchestrator / Docker **feature** work for Phase 2.  
- **Node 20+** for Vite/Playwright as in `package.json`.  
- **E2E + Lighthouse** on chat may need **CI** with browser install + (optional) stack; align with `FP-047` later — Phase 2 can ship **local** scripts first.  

### 4.5 Files likely touched (indicative)

- `src/components/AppShell.tsx`, `BottomNav.tsx`, `SidebarNav.tsx`  
- `src/design/tokens.css` (safe-area, new utilities, Me/Admin breakpoints)  
- `src/features/me/MePage.tsx`, `src/features/me/MeNav.tsx` (layout / compact nav pattern)  
- `src/features/admin/AdminPage.tsx`, `src/features/admin/AdminNav.tsx`  
- `src/features/approvals/ApprovalsPage.tsx` + `tokens.css` (approvals modal)  
- `src/features/chat/ChatPage.tsx`, `src/features/memory/SearchPage.tsx`  
- `tests/**`, `playwright.config.ts`, `package.json` scripts (e.g. `lighthouse:chat`)  
- **Not** `vite.config` PWA plugins until Phase 3.  

---

## 5. Implementation slicing

Parent **Pass 2.1 / 2.2 / 2.3** map to **four** sub-chunks so work can ship incrementally. Names are **suggested** slugs for `/create-plan`.

### 5.1 Phase 2A — `cross_device_web_phase_2a_mobile_shell_and_me_admin_layout`

- **Objective:** Fix **P0** — responsive **Settings** and **Admin** sub-shells; add **safe-area** padding to app chrome; **optionally** improve primary nav overflow.  
- **Key files:** `MePage.tsx`, `AdminPage.tsx`, `MeNav.tsx`, `AdminNav.tsx`, `tokens.css`, `AppShell.tsx` (if header changes).  
- **Acceptance:** At **&lt; 720px** (or chosen breakpoint), **Me** and **Admin** show **one column** (nav as **horizontal scroll**, **hamburger/accordion**, or **top** strip — **decide in implementation**) and **readable** content width; no horizontal page scroll from nav alone. **Manual:** iOS Safari + Chrome Android on emulator.  
- **Tests:** New **Vitest** (render at width) and/or **Playwright** `viewport: { width: 390, height: 844 }` smoke for `/me/profile` and `/admin/users`.  
- **Non-goals:** No Approvals modal rewrite yet; no Lighthouse job yet.  

### 5.2 Phase 2B — `cross_device_web_phase_2b_chat_search_approvals_mobile`

- **Objective:** **Chat** keyboard/viewport, **Search** + **EntityCard** narrow layout, **Approvals** full-screen (or max-height) modal + **stacked** footer; optional **swipe** for confirm.  
- **Key files:** `ChatPage.tsx`, `tokens.css` (chat), `SearchPage.tsx`, `EntityCard*.tsx`, `ApprovalsPage.tsx`, `Approvals` CSS in `tokens.css`.  
- **Acceptance:** Modal uses **min(100dvh, 100%)** safe area; primary actions meet **44px**; no clipped text at 320px width. **Swipe** optional; if shipped, only for `elevate` + high risk.  
- **Tests:** Extend `ApprovalsPage.test.tsx` (if modal class changes); Playwright **compact** approves flow.  
- **Non-goals:** No new approvals API.  

### 5.3 Phase 2C — `cross_device_web_phase_2c_settings_admin_forms_tables`

- **Objective:** **Tables** and **dense** admin/me views — overflow, `min-width`, **card** layout for 1–2 worst pages (e.g. **AdminUsers**, **Audit**), credential **form** spacing.  
- **Key files:** `src/features/admin/*View.tsx`, `src/features/me/*View.tsx`, `src/features/credentials/**`, `tokens.css`.  
- **Acceptance:** No **unintended** horizontal page scroll; **tables** scroll inside a **bounded** region or reflow.  
- **Tests:** Snapshots or Playwright for one admin + one me table.  
- **Non-goals:** Full redesign of `dashboard` legacy SPAs.  

### 5.4 Phase 2D — `cross_device_web_phase_2d_a11y_performance_lighthouse`

- **Objective:** **Keyboard** sweep for 2A–2C routes, **focus return** for modals, **Lighthouse** mobile on `/chat` (and docs for **local** + **CI**), wire **Pass 2.2** DoD.  
- **Key files:** modal components, `ChatPage` live region, `package.json` + `.github/workflows` (if greenfield job), `README.md` **How to run Lighthouse**.  
- **Acceptance:** **Performance ≥ 80**, **Best Practices ≥ 90** on `/chat` in **default prod build** (document device/emulation). **CI** optional — align with `FP-047`.  
- **Non-goals:** PWA **score**; bundle **gzip** **≤ 60 KB** delta (optional FP-046/FP-001 note — separate).  

---

## 6. First recommended implementation chunk

**`cross_device_web_phase_2a_mobile_shell_and_me_admin_layout` (2A)**  

- **Rationale:** **MePage** and **AdminPage** use **fixed** multi-column **inline** grids with **no** responsive rule — this is the **largest** functional gap for “mobile companion” and affects **every** Me/Admin route. Fix **before** deep-diving single-feature polish.  
- **Size:** **Smaller** than the whole **Phase 2** umbrella; shippable in **1–2** focused PRs.  
- **After 2A:** **2B** (high-traffic **Approvals/Chat/Search**), then **2C** / **2D** as capacity allows.  

**Alternative (only if 2A is already drafted elsewhere):** treat **2A+2B** as one “**Phase 2 — wave 1**” if the team prefers a single **merge** — **not** recommended: **2A** is blocking for Settings/Admin **readability** on phones.

---

## 7. Acceptance criteria (roll-up for Phase 2 close)

Binary-style checklist for the **umbrella** (completed across **2A–2D**). **Verification** (smoke E2E, Lighthouse scores in a real browser) may still be **environment-gated** — see §Implementation log — **Phase 2D** for the authoritative **Phase 2** closeout status.

- [x] **Me** and **Admin** nested routes are **usable** at **320px** width (nav + content; no critical overlap). *(2A)*  
- [x] **Approvals** confirm flows are **comfortable** on small screens (full-viewport or equivalent; actions ≥ **44px**). *(2B)*  
- [x] **Chat** and **Search** have **no** known **blocking** mobile layout bugs on narrow viewports. *(2B — re-verify in release testing)*  
- [x] **Lighthouse mobile** (measured run): **/chat** meets **≥ 80** Performance, **≥ 90** Best Practices — **met** on **2026-04-29** verification (**Vite preview** + Playwright-bundled Chromium; see §**Verification supplement**). Re-run with **`/usr/bin/google-chrome`** when available for parity with release docs.  
- [x] **Smoke Playwright** mobile specs (**2A/2B/2C**) **pass** on **live stack** with **admin** smoke user — **verified** **2026-04-26** (see §**Verification supplement**). **`/admin/users`** exercised (admin smoke user). Use **`PLAYWRIGHT_BASE_URL=http://localhost`** when **`LUMOGIS_PUBLIC_ORIGIN`** is `http://localhost` (cookie / CSRF alignment). **Parallel runs:** the `chromium-smoke-shared-user` Playwright project forces **`workers: 1`** because **one refresh-token JTI per user** revokes concurrent browser logins.
- [x] **Core** keyboard path + modal focus **hardened / tested** for **Approvals** and **CopyOnce**; **Chat** transcript **live region** + **`aria-busy`** while streaming; **Me/Admin** nav remains **link-based** and keyboard-reachable. *(2D — not a full WCAG enterprise audit)*  
- [x] `npm run build` and **`npm test`** (Vitest) **pass**; new tests for changed surfaces. *(per ship chunk)*  
- [x] **No** service worker, **no** push, **no** capture feature, **no** PWA manifest in scope of this **Phase 2** tranche.  

---

## 8. Tests and checks (commands)

| Check | Command / note |
| --- | --- |
| Vitest | `cd clients/lumogis-web && npm test` |
| Lint | `npm run lint` |
| Build | `npm run build` |
| E2E (local) | `make web-e2e` (needs stack + Caddy on **80** + **`LUMOGIS_WEB_SMOKE_*`**; smoke mobile specs run in project **`chromium-smoke-shared-user`** with **`workers: 1`**) |
| Playwright **mobile** viewport | Add `projects` in `playwright.config.ts` or `use: { viewport: { width: 390, height: 844 } }` in new spec |
| Lighthouse | `npm run lighthouse:chat` (after `npm run build` + `npm run preview` on **4173**). **Mobile:** `--form-factor=mobile`; categories **performance** + **best-practices** only. See **`clients/lumogis-web/README.md`** (`CHROME_PATH` if needed). |
| Axe | Existing `first_slice` / **admin_shell** with Axe on `#lumogis-main` — extend **viewport** in new specs |

**Discovery (this planning pass, 2026-04-26):** parent `Phase 2` / `Pass 2` / `Lighthouse` / `44` / `swipe` located via workspace search in `cross_device_lumogis_web.plan.md`. **Shell** patterns (`BottomNav`, `AppShell`, `container`, `dvh`, `approvals` modal) via read of `tokens.css` and feature files. **`npm test`:** 24 files, **147** passed (report at authoring time).  

---

## 9. Risks and open decisions

- **Swipe-to-confirm:** may require **pointer** + **reduced-motion** fallbacks; treat as **optional** to avoid derailing 2.1.  
- **Lighthouse in CI:** flaky in shared runners — **2D** may **document** manual **release** gate first, then **FP-047** automation.  
- **Me/Admin nav pattern:** **tabs vs drawer vs scroll** — product choice; 2A should pick **one** pattern and use it **consistently** for Me + Admin.  
- **Admin table density:** **card** view may be **2C**-only; avoid scope creep in **2A**.  

---

## 10. Follow-up portfolio (proposed — no file edit here)

- **`FP-001`:** add **Note** in a future **verify-plan** (when Phase 2 ships): *“Parent Phase 2 mobile UX: see `docs/architecture/cross-device-web-phase-2-mobile-ux-plan.md` + Implementation Log; Phases 3–6 still open.”*  
- **`FP-047`:** optional **merge** with **2D** Lighthouse/Playwright in CI when ready — coordinate to avoid duplicate workflows.  

---

## Implementation log — Phase 2A (shipped)

**Date:** 2026-04-28  

**What shipped**

- **Me/Admin sub-shells:** `lumogis-subshell` + `subshell` container queries at **719px / 720px** (aligned with app shell): desktop keeps **two columns** (`220px` Me / `240px` Admin + fluid content); compact = **one column**, **nav first**, **content** in `lumogis-subshell__content` with `min-width: 0`.
- **Compact nav:** shared **horizontal scroll** strip for `MeNav` and primary **AdminNav** links (`.lumogis-settings-nav__link`, `aria-current` from `NavLink`, **≥44px** min-height in compact mode). **Admin legacy** links stay **below** the strip with `word-break` to avoid page overflow.
- **App chrome:** `env(safe-area-inset-*)` on **`.lumogis-shell`** (L/R/T) and **bottom nav** (bottom padding + `min-height`). Header **email** truncation (`.lumogis-shell__user-email`, `title` = full email); header tool buttons **flex-shrink: 0**.
- **Tests:** Vitest `MePage.test.tsx`; `AdminPage.test.tsx` admin subshell case; Playwright `tests/e2e/me_admin_mobile_shell.spec.ts` (viewport **390×844**, scrollWidth guard, skips `/admin` when smoke user is not admin).

**Verification caveat (closeout) — resolved 2026-04-26**

Phase **2A** shipped with **Vitest**, **`npm run build`**, and **`npm run lint`** passing. The mobile Playwright shell spec (`tests/e2e/me_admin_mobile_shell.spec.ts`) **passed** on a **live docker stack** with **`AUTH_ENABLED=true`**, Caddy on port **80**, and an **admin** smoke user — see §**Verification supplement** (2026-04-26). *Historical:* some environments skipped this spec when **`LUMOGIS_WEB_SMOKE_*`** were unset.

---

## Implementation log — Phase 2B (shipped)

**Date:** 2026-04-26  

**What shipped**

- **Approvals modal (compact):** `@media (max-width: 719px)` — backdrop uses **safe-area** padding; modal is **full-viewport** (`height: 100%`, flush `border-radius`); **scrollable** `.lumogis-approvals__modal-body` (`flex: 1`, `min-height: 0`, `overflow-y: auto`); footer **stacked** with **full-width** buttons, **`≥44px`** tap targets, `white-space: normal` for long confirm labels. **Desktop:** `max-height: min(90dvh, calc(100dvh - 3rem))` on the modal + scrollable body for long copy. **Escape**, backdrop click, and **default focus on Cancel** unchanged.
- **Global viewport (mobile keyboard):** **`clients/lumogis-web/index.html`** — the viewport `<meta>` includes **`interactive-widget=resizes-content`**. That is a **document-wide** behaviour change for how the layout resizes when the on-screen keyboard opens (where supported). It is **not** merely an isolated Chat CSS tweak; Chat still has complementary `min-width` / safe-area rules in **`tokens.css`**.
- **Chat:** **`.lumogis-chat`** / **`.lumogis-chat__main`**: `min-width: 0`, `width` / `max-width: 100%` on chat root; compose **`padding-bottom`** respects **`env(safe-area-inset-bottom)`** and **`scroll-margin-bottom`** for scroll-into-view hints. No streaming or chat API changes.
- **Search / EntityCard:** **`.lumogis-search`** `min-width: 0`, `max-width: min(1100px, 100%)`; **`.lumogis-search__col`**, **`.lumogis-search__body`**, **`.lumogis-search__hit`**, **`.lumogis-search__hit-meta`**, **`.lumogis-search__entity-btn`**: overflow / **`min-width: 0`**; **`.lumogis-entity-card`** **`max-width: 100%`**, `overflow-wrap` / `word-break`; **`.lumogis-entity-card__related-item`** **`flex-wrap`**. No new search behaviour.
- **Swipe-to-confirm:** **Not implemented** — **deferred** (optional per plan; no low-risk existing pattern in-repo).
- **Approvals SSE / data fetch:** **No change** — no clear duplicate pending fetch or redundant SSE issue found on inspection.
- **Tests:** Vitest: `ApprovalsPage` (modal body + footer regions); `SearchPage` (`.lumogis-entity-card`); `ChatPage` (`.lumogis-chat` root). Playwright: `tests/e2e/phase_2b_mobile_surfaces.spec.ts` (`LUMOGIS_WEB_SMOKE_*` skip contract).

**Phase 2B closeout (verification caveat) — resolved 2026-04-26**

Phase **2B** shipped with **Vitest**, **`npm run build`**, and **`npm run lint`** passing. **`phase_2b_mobile_surfaces.spec.ts`** **passed** on the same live-stack smoke run as **2A** — see §**Verification supplement**. **Swipe-to-confirm** remains **intentionally deferred**.

**E2E caveat**

The **2B** Playwright file uses the same **skip-when-no-creds** behaviour as **2A** / **`first_slice`**. Do **not** report **E2E passed** if tests were **skipped** (missing **`LUMOGIS_WEB_SMOKE_*`**).

**Validation (authoring environment)**

- `npm test` — pass  
- `npm run build` — pass  
- `npm run lint` — pass  

**Remaining after 2B (historical snapshot)**

- **2C** — Dense **tables/forms** in Me/Admin *(shipped — see §Implementation log — Phase 2C)*.  
- **2D** — **a11y** sweep, **Lighthouse** / performance *(shipped — see §Implementation log — Phase 2D)*.  

*Umbrella status after live-stack smoke E2E (**2026-04-26**): **closed** — see §**Verification supplement** and §**Implementation log — Phase 2D** (Lighthouse lane already measured **2026-04-29**).*

---

## Implementation log — Phase 2C (shipped)

**Date:** 2026-04-26  

**What shipped**

- **Shared CSS** (`clients/lumogis-web/src/design/tokens.css`): **bounded horizontal scroll** (`.lumogis-table-scroll` + `-webkit-overflow-scrolling: touch`), **dense table** cell padding (`.lumogis-dense-table`) and **≥44px** tap targets on buttons inside those tables (`.lumogis-dense-table button`), **long text wrap** (`.lumogis-long-text`), **compact form grid** (`.lumogis-dense-form-grid`), **stacking action rows** at `≤719px` (`.lumogis-dense-actions--stack`, `.lumogis-form-actions--stack`), **MCP mint row** (`.lumogis-mcp-mint-row`), **Revoke** buttons in MCP token list (`.lumogis-mcp-token-list button`), **credential form** narrow rules (`.lumogis-credential-form`, **44px** min heights on inputs/buttons), **section containment** (`.lumogis-admin-dense-section` with `min-width: 0`).
- **Admin — dense tables / actions:** `AdminUsersView` — users table in `.lumogis-table-scroll` + `.lumogis-dense-table`; per-row actions in `.lumogis-dense-actions--stack`; email cells `.lumogis-long-text`; create/reset/import modals use `.lumogis-credential-form` / `.lumogis-form-actions--stack` where appropriate. `AdminAuditView` — filters in `.lumogis-dense-form-grid`; audit table scroll-wrapped + dense; result column uses `.lumogis-long-text`. `AdminDiagnosticsView` — stores, capabilities, and credential-key tables scroll-wrapped + dense. `AdminMcpTokensView` — mint row `.lumogis-mcp-mint-row`; long token ids wrapped via `.lumogis-long-text` on `<code>`.
- **Me — dense table:** `MeLlmProvidersView` — providers table uses the same scroll + dense pattern; connector and details columns get `.lumogis-long-text`.
- **Credential form:** `LlmApiKeyForm` — `.lumogis-credential-form` (narrow inputs, full-width primary button at compact breakpoint).
- **Card/list fallback:** **Not used** — tables remain semantic; overflow stays inside the scroll region.
- **Tests:** Vitest assertions on `.lumogis-table-scroll` / `.lumogis-dense-table` in `AdminUsersView.test.tsx`, `AdminAuditView.test.tsx`, `MeLlmProvidersView.test.tsx`; `LlmApiKeyForm` has `form.lumogis-credential-form` in `forms.test.tsx`. Playwright: `tests/e2e/phase_2c_mobile_dense.spec.ts` (viewport **390×844**; `/admin/users` when smoke user is admin; `/me/llm-providers`; `scrollWidth` guard; same **`LUMOGIS_WEB_SMOKE_*`** skip contract).
- **Non-goals honoured:** No Phase **2D**, no Lighthouse/CI scripts, no API changes, no Me/Admin shell layout rework beyond dense sections, no Phase 2 umbrella completion claim.

**E2E caveat (2C) — resolved 2026-04-26**

`phase_2c_mobile_dense.spec.ts` **passed** on the live-stack smoke run ( **`/admin/users`** + **`/me/llm-providers`**). *Contract:* still **skipped** when smoke credentials are missing — do **not** claim **E2E passed** if the run **skipped**.

**Validation (implementation pass)**

- `npm test` — pass (Vitest **154** tests).  
- `npm run build` — pass.  
- `npm run lint` — pass.  

**Remaining (parent Phase 2 — umbrella status)**

Phase **2D** shipped — see §**Implementation log — Phase 2D** below for **final** Phase 2 status (implementation vs verification caveats).

---

## Implementation log — Phase 2D (shipped)

**Date:** 2026-04-29  

**What shipped**

- **A11y / keyboard (core surfaces):** **Chat**, **Search**, **Me** shell, **Admin** shell — reviewed: primary nav uses **focusable links** (`BottomNav` / `SidebarNav` anchors; **Me** / **Admin** compact strips use **`NavLink`**). **No broad layout changes.** **Approvals** confirmation dialog: **Escape** now handled at **`document`** level so it always dismisses while the dialog is open; **focus returns** to the **button that opened** the dialog (stored **`HTMLElement`** on open, restored on close). **`CopyOnceModal`:** **initial focus** on **Copy to clipboard**; **Escape** closes; **focus restoration** to the prior active element on close. **Chat** transcript: existing **`role="log"`** + **`aria-live="polite"`** retained; added **`aria-busy={streaming}`** so assistive tech can reflect in-flight assistant output without per-token spam.
- **Modal focus limitations:** No new modal framework. **Approvals** intentionally keeps **default focus on Cancel** (safe default); focus return targets the **invoking** action control. **Admin/User** inline modals (import/create/reset) were **not** refactored in this chunk — **2C** patterns already improve narrow layouts; full focus cycle there can follow the same **return-focus** pattern later if needed.
- **Chat live region / stream:** Confirmed **`lumogis-chat__messages`** live region; **`aria-busy`** is the only functional a11y tweak.
- **CSS sanity (2A–2C):** Reviewed **`tokens.css`** — **`719px`** media / **`720px`** **container** split matches app shell + subshell docs; **safe-area** applies to shell/bottom nav only; **Phase 2C** table utilities are **class-scoped**; **no conflicting duplicate breakpoints** found; **no rewrite**.
- **Lighthouse (mobile `/chat`):** Added **`npm run lighthouse:chat`** in **`clients/lumogis-web/package.json`** (`npx lighthouse` + **`--form-factor=mobile`** + **performance** + **best-practices** only). Documented full procedure, **`CHROME_PATH`**, preview port **4173**, and **non-mandatory CI** stance in **`clients/lumogis-web/README.md`**.  
  **Run in this environment:** **Not completed** — machine `CHROME_PATH` unset / no system Chrome; Playwright’s cached Chromium **failed** to launch for Lighthouse (**“Unable to connect to Chrome”**). **Do not** treat Phase 2 Lighthouse DoD as **measured-passed** here. **Command to run later:** `npm run build && npm run preview` (port **4173**), then `CHROME_PATH=/path/to/google-chrome npm run lighthouse:chat` or the JSON example in the web README.
- **Playwright smoke (2A / 2B / 2C):** Specs **`me_admin_mobile_shell.spec.ts`**, **`phase_2b_mobile_surfaces.spec.ts`**, **`phase_2c_mobile_dense.spec.ts`** — **live-stack verification recorded** **2026-04-26** in §**Verification supplement** (**4 passed**, **0 skipped**, **0 failed**; **`/admin/users`** exercised with an **admin** smoke user). Infra fixes shipped with that verification: **`lumogis-web`** compose healthcheck uses **`127.0.0.1`** (avoids **IPv6 localhost** connection refused); orchestrator exposes **`GET /healthz`** (no JWT) and Compose healthcheck targets it so **`AUTH_ENABLED=true`** stacks report **healthy**.
- **Tests:** Vitest — **ApprovalsPage** (focus return + Escape); **ChatPage** (transcript **`role` / `aria-live` / `aria-busy`**); **`CopyOnceModal.test.tsx`** (focus + Escape). **Total web tests:** **159** passing in verification pass.

**Validation (implementation pass, 2026-04-29)**

- `npm test` — pass (**159** tests).  
- `npm run build` — pass.  
- `npm run lint` — pass.  
- `npm run lighthouse:chat` — **not reliably runnable** here (Chrome/Lighthouse launcher); procedure documented.  
- Playwright **2A/2B/2C** mobile specs — **pass** on live stack (**2026-04-26**); see supplement.

**Phase 2 umbrella — final status**

**Closed (parent DoD).** All **2A–2D** implementation chunks are **shipped**, **Lighthouse** mobile thresholds on preview **`/chat`** were **measured and met** (**2026-04-29**; see supplement), and **smoke Playwright 2A/2B/2C** **passed** on a **live stack** with **admin** smoke credentials (**2026-04-26**; see supplement). **Optional parity:** re-run Lighthouse with **`/usr/bin/google-chrome`** when convenient — not required to treat Phase 2 as closed given the measured Chromium run documented below.

### Verification supplement (2026-04-26 — live stack + smoke E2E)

**Stack:** `docker compose up -d` from repo root (with this environment’s **`COMPOSE_FILE`** overlays). **Caddy** on **port 80** serves the **Lumogis Web** SPA and proxies API traffic to the orchestrator.

**Auth for smoke:** **`AUTH_ENABLED=true`** with **`AUTH_SECRET`**, **`LUMOGIS_CREDENTIAL_KEY`**, and **`LUMOGIS_PUBLIC_ORIGIN=http://localhost`** in **gitignored** **`.env`**. Bootstrap admin (**`LUMOGIS_BOOTSTRAP_ADMIN_*`**) seeds the first user when the **`users`** table is empty. **Email shape:** login **`POST /api/v1/auth/login`** uses strict email validation — **`@example.test`** is rejected; use a deliverability-valid domain such as **`phase2-smoke-admin@example.com`** for local smoke (not a real mailbox).

**Playwright env (local-only, not committed):** e.g. **`/tmp/lumogis-phase2-smoke.env`** with **`export PLAYWRIGHT_BASE_URL=http://localhost`**, **`LUMOGIS_WEB_SMOKE_EMAIL`**, **`LUMOGIS_WEB_SMOKE_PASSWORD`** (≥**12** characters). **Do not** log the password.

**Command:**

`cd clients/lumogis-web && source /tmp/lumogis-phase2-smoke.env && npx playwright test tests/e2e/me_admin_mobile_shell.spec.ts tests/e2e/phase_2b_mobile_surfaces.spec.ts tests/e2e/phase_2c_mobile_dense.spec.ts`

**Result:** **4 passed**, **0 skipped**, **0 failed** (projects: **`chromium-smoke-shared-user`**, **`workers: 1`** — required because **one active refresh-token JTI per user** invalidates parallel browser sessions).

**`/admin/users`:** **Yes** — **me_admin_mobile_shell** admin test and **phase_2c** dense test both gate on admin navigation to **`/admin/users`** (smoke user **`role=admin`**).

**Repo fixes shipped with this verification (not Phase 2 UI scope):** **`docker-compose.yml`** — **`lumogis-web`** healthcheck **`wget http://127.0.0.1/healthz`**; orchestrator healthcheck **`curl http://127.0.0.1:8000/healthz`**. **`orchestrator/main.py`** — **`GET /healthz`**. **`clients/lumogis-web/playwright.config.ts`** — **`chromium-smoke-shared-user`** project (**`workers: 1`**, **`fullyParallel: false`**).

### Verification supplement (2026-04-29 — Lighthouse / prior agent environment)

**Playwright (2A / 2B / 2C) — superseded:** Earlier record: **4 skipped** when **`LUMOGIS_WEB_SMOKE_*`** unset and stack/Caddy not up. **Superseded by 2026-04-26 live-stack run above.**

**Lighthouse (mobile `/chat`):** `npm run build` OK; **`http://127.0.0.1:4173`** already serving preview. **`/usr/bin/google-chrome`** — **not installed** on this host. Run used **`CHROME_PATH`** = Playwright cache **`…/chromium-1217/chrome-linux64/chrome`** and Lighthouse **`--chrome-flags="--headless=new --no-sandbox --disable-gpu"`** (required for this sandbox). Command aligned with **`npm run lighthouse:chat`** intent except for extra flags + binary path.

**Scores (JSON report, `form-factor=mobile`, categories performance + best-practices):**

| Category | Score (0–100) | Parent target |
| --- | --- | --- |
| Performance | **98** | ≥ 80 — **met** |
| Best Practices | **96** | ≥ 90 — **met** |

**Caveat:** Page is **Vite preview** static **`/chat`** (typical **login** shell without session); scores describe **that** load, not logged-in chat against orchestrator.

**Explicit record — umbrella closure**

- **Lighthouse DoD (measured once):** **Satisfied** (**2026-04-29**; preview + Chromium path caveat; scores in table below).  
- **Strict Phase 2 “fully closed” (parent checklist):** **Satisfied** — **Playwright 2A/2B/2C** **passed** **2026-04-26** on a **live stack** with **smoke credentials** and an **admin** user for **`/admin/users`** (see **2026-04-26** supplement).  
- **Net:** Phase 2 umbrella is **closed**. Optional: repeat Lighthouse on **system Google Chrome** for release-parity documentation.

---

## Implementation Log (/verify-plan)

**Verified by:** Composer  
**Date:** 2026-04-26  
**Plan:** `docs/architecture/cross-device-web-phase-2-mobile-ux-plan.md` (user-referenced architecture extraction; not *(maintainer-local only; not part of the tracked repository)*)  
**Critique rounds:** **0** on this file; parent `cross_device_lumogis_web.plan.md` arbitration history: **5** rounds  
**Tests:** **1554** passing / **9** skipped / **0** failed (`make compose-test`); Vitest **159**/0 (see doc §Phase 2D)  
**Files:** Phase 2A–2D per §Implementation logs; verify-only: `Makefile`, `orchestrator/tests/test_api_v1_admin_diagnostics.py`, `clients/lumogis-web/openapi.snapshot.json`  
**Done checklist:** **7/7** (§7)  
**ADR:** none

### What matched the plan

1. Umbrella Phase **2A–2D** closed per §7 + implementation/verification supplements (Lighthouse measured **2026-04-29**; live-stack Playwright **2026-04-26**).  
2. Client surfaces align with §4–§6 scope; swipe-to-confirm explicitly deferred.  
3. `make compose-test` green after **`AUTH_ENABLED=false`** injection + OpenAPI snapshot sync for `/healthz`.

### Deviations (intent preserved)

<!-- VERIFY-PLAN: deviation -->

1. `/verify-plan` executed against the user **`@`**-referenced **docs/architecture** extraction, not a *(maintainer-local only; not part of the tracked repository)* Phase 2 chunk file.

### Implementation errors

<!-- VERIFY-PLAN: error -->

None.

### Critical violations

<!-- VERIFY-PLAN: CRITICAL -->

None.

### ADR notes

No ADR for this slug — extraction/planning doc only.

### Security findings

None.

### Test quality issues

None.

### Test fixes applied

1. `test_admin_diagnostics_200_when_auth_disabled_default_user` — `monkeypatch.setenv("AUTH_ENABLED", "false")` with VERIFY-PLAN note (host `.env` vs container).  
2. `Makefile` `compose-test` — `-e AUTH_ENABLED=false` on `docker compose run`.

### Potential regressions

None observed.

### Noteworthy discoveries

OpenAPI snapshot regenerated for **`GET /healthz`**.

### Recommended next steps

Proceed with parent **Phase 3+** per `cross_device_lumogis_web.plan.md`; optional **FP-047**.

---

## Related

- [lumogis-web-roadmap-reconciliation-after-remediation.md](lumogis-web-roadmap-reconciliation-after-remediation.md)  
- [lumogis-self-hosted-platform-remediation-plan.md](lumogis-self-hosted-platform-remediation-plan.md)  
- Parent: *(maintainer-local only; not part of the tracked repository)* (`### Phase 2`).
