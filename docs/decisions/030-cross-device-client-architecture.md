# ADR: Cross-device client architecture (server-brained, multi-surface)

**Status:** Finalised (Phase 4 Web Push programme verified **2026-04-29**)
**Created:** 2026-04-17
**Last updated:** 2026-04-29 (+ Phase 5 Capture confirmation)
**Decided by:** /explore (Composer)
**Finalised by:** `/verify-plan`-style closeout — Phase **4A–4E** Web Push verification (**2026-04-29**; operator notes in **this ADR**, **`clients/lumogis-web/src/pwa/README.md`**, and **`docs/LUMOGIS_REFERENCE_MANUAL.md`** §13).

**Draft mirror:** *(maintainer-local only; not part of the tracked repository)* (kept in sync with status history)

## Context

Lumogis intelligence, storage, and tool execution live on a **self-hosted backend** (FastAPI orchestrator, Qdrant, Postgres, graph service direction, MCP). LibreChat provides chat UI today but does not fully express Lumogis-specific flows (companion mobile, unified approvals, bounded offline behavior). The product needs **one brain, multiple clients** without mirroring the KG on devices or building offline-first local intelligence on mobile.

## Decision

Ship **Lumogis Web** as a **responsive browser application** backed by a **versioned client HTTP façade** implemented **in-process** on the orchestrator (`/api/v1/…`), reusing existing chat, memory, actions, and SSE infrastructure. Add a **PWA layer** (manifest + service worker) **after** core responsive flows work, using **narrow static precache** and **TanStack Query–style persistence** for drafts/recent reads—not full offline graph operation. Treat **mobile as a companion** and **desktop browser as the power surface**. **Defer** a **Tauri desktop shell** and a **separate BFF microservice** until concrete triggers (OS integration needs, measured multi-client aggregation pain, or enterprise distribution constraints).

### Phase 4 addendum — browser Web Push (verified 2026-04-29)

**Additive delivery** (does not replace the **`Notifier`** / **ntfy** path): **`pywebpush`** + **VAPID** on the orchestrator; **`PushManager`** + explicit user-gesture **`Notification.requestPermission`** in Lumogis Web; **minimal JSON** payloads; service worker **`push`**/**`notificationclick`** with **same-origin** allowlisted navigation; **Workbox `injectManifest` precache-only** — **no `runtimeCaching`**, **no** caching orchestrator **`/api/*`** responses in Cache Storage policy. **`ACTION_EXECUTED` → Web Push** remains **explicitly deferred** until a safe generic template exists (hooks carry connector/action identifiers). Full operator notes: **`clients/lumogis-web/src/pwa/README.md`** (Phase 4) and **`clients/lumogis-web/README.md`**.

## Alternatives Considered

- **Responsive web only** — Faster start but weaker mobile engagement and resilience; rejected as **long-term sole** approach, acceptable as Phase 1 stepping stone.  
- **PWA-first / offline-first intelligence** — Conflicts with architecture (“no local brain”); rejected.  
- **Capacitor/React Native** — Higher cost; defer until web platform gaps are proven.  
- **Dedicated BFF Docker service** — Extra operational burden; rejected for v1 per small-team constraint.  
- **Desktop shell (Tauri) first** — Slows API contract validation; rejected for initial rollout.

Full analysis: *(maintainer-local only; not part of the tracked repository)*

## Consequences

**Easier:** Single deployment story for v1; reuse Ask/Do and audit; reuse `GET /events`; consistent security boundary; fastest path to mobile-usable UX.

**Harder:** Browser/PWA limitations on background SSE and push require careful UX and parallel **ntfy** channels; avoiding accidental caching of private API responses requires discipline in SW design (precache static hashed assets only; runtime route caching forbidden).

**Future chunks must know:** Client DTOs live behind `/api/v1/`; LibreChat may remain transitional; graph access should be **proxied** through orchestrator for centralized auth.

## Revisit conditions

- **Two** of: global hotkey/tray requirement, filesystem workflows beyond browser, enterprise blocks browser installs, or PWA push reliability fails approval SLA → **revisit Tauri** (or enterprise-native wrapper).  
- **Measured** API chattiness or incompatible payloads across **multiple third-party clients** → **revisit separate BFF** or GraphQL-style aggregation.  
- Apple/Google policy forces store presence for core persona → **revisit Capacitor**.

## Status history

- 2026-04-17: Draft created by /explore
- 2026-04-23: Phase 0 implementation confirmed the decision (`/api/v1/*` in-process façade shipped, no BFF, no PWA, no Tauri). ADR remains Draft; finalisation deferred until the multi-phase rollout completes.
- 2026-04-23: Phase 1 Pass 1.1 (client foundation) confirmed — ADR remains Draft per multi-phase finalisation policy.
- 2026-04-24: Phase 1 Passes 1.2–1.5 + Phase 1 closure re-confirmed — ADR Draft; `docs/decisions/` deferred to Phase 4 per plan.
- 2026-04-29: **Phase 4 Web Push programme (4A–4E)** verified — PWA **`injectManifest`** + **`sw.ts`** **`push`**/**`notificationclick`** + VAPID/**`pywebpush`** + Me notifications opt-in documented; **`runtimeCaching`** absent; **`ACTION_EXECUTED`** push intentionally not wired (follow-up **FP-053**). **Manual end-to-end Web Push exercises** — operator checklist in **`clients/lumogis-web/src/pwa/README.md`** — not executed in headless CI (documented caveat). Finalised copy: **this file** (`docs/decisions/030-cross-device-client-architecture.md`). Parent plan **`cross_device_lumogis_web`** Phase **5–6** and optional **LibreChat compose-profile deprecation** (:: Pass 4.3) remain **out of scope** for this verification pass.
- 2026-04-29: **Phase 5 Capture / QuickCapture MVP** implementation confirmed — bounded IndexedDB staging + manual sync (**no** silent server mutation, **no** SW API caching for capture payloads), explicit **Add to memory** / **`POST …/index`** only, STT via existing server facade, export/import includes capture metadata + **`captures/media/`** binaries. **Does not** change the core decision (server-brained, **no** offline-first local brain). **Phase 6 (Tauri)** and §21 **FP-TBD-5.*** follow-ups remain **out of chunk** scope.
