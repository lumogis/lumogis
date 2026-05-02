# PWA packaging (`src/pwa`)

## Phase 3A — installability metadata (`public/`)

The web app manifest and launcher icons live under **`clients/lumogis-web/public/`** so Vite copies them into **`dist/`** at the site root (`/manifest.webmanifest`, `/icons/*`).

Regenerate launcher icons:

```bash
python3 scripts/generate-pwa-icons.py
```

## Phase 3B — static bundle precache (`sw.ts`)

- **Source:** `src/pwa/sw.ts` — Workbox **`injectManifest`**: **`precacheAndRoute(self.__WB_MANIFEST)`**, **`cleanupOutdatedCaches()`**, **`skipWaiting`/`clientsClaim`** (updates apply without bespoke in-app prompts yet).
- **Phase 4D:** **`push`** and **`notificationclick`** listeners (**no** **`fetch`** of APIs in **`push`**; **no** Cache Storage/`IndexedDB` in handlers — see **`swPush.ts`**). Payload JSON aligns with **`orchestrator/services/webpush.py`** (**`title`**, **`body`**, **`url`**) plus optional **`tag`**; **`url`** is sanitized to **`/`**, **`/chat`**, **`/approvals`**, **`/me/notifications`** only.
- **Phase 5H (QuickCapture PWA entry):** **`public/manifest.webmanifest`** adds a launcher **shortcut** (“Quick capture” → **`/capture`**) and an optional **`share_target`** using **`GET`** to **`/capture`** with `title`, `text`, and `url` query parameters. **`QuickCapturePage`** reads those params once (via **`useSearchParams` / `useLayoutEffect`**), prefills the form, strips the query string ( **`replace: true`** ), and shows a status line — **no automatic** `POST /api/v1/captures`, **no** outbox sync, **no** Add to memory. Non-**http(s)** shared URLs are dropped. **No** `share_target` **POST** (would require SW **`fetch`** or server form handling — out of scope). Service worker **`sw.ts`** is unchanged (**precache + push** only).
- **Phase 5I (user export):** per-user backup ZIPs from **`user_export`** include **`captures`**, **`capture_attachments`**, **`capture_transcripts`**, and attachment **binaries** under **`captures/media/`** (see capture plan §21.1); import restores blobs after Postgres. Not a substitute for real backup/DR — operator feature for portability.
- **Build:** **`vite-plugin-pwa`** (`strategies: injectManifest`, `manifest: false`). Output is **`dist/sw.js`** (ES module worker). **`runtimeCaching`** is intentionally absent — orchestrator/API paths (`/api/*`, `/events`, `/v1/*`, etc.) must never be cached here.
- **Registration:** `registerServiceWorker.ts` imports from `main.tsx` and calls **`navigator.serviceWorker.register('/sw.js', { type: 'module' })`** only when **`import.meta.env.PROD`** (skipped in **`vite dev`** and Vitest).
- **Inspection:** Production build (`npm run build` → **`npm run preview`**) → Chrome DevTools → **Application** → **Service Workers** / **Cache Storage** (look for **`lumogis-web`**-prefixed Workbox caches). Compare **`dist/sw.js`** to verify no **`registerRoute`** / **`runtimeCaching`** for APIs.
- **Reset local debugging:** Application → Service Workers → **Unregister**, or Storage → **Clear site data**.

This is **install-time / revisit performance** for the shell bundle — **not** “Lumogis works offline”: chat, search, approvals, and private data still require the live orchestrator.

## Phase 3C — composer draft storage (`drafts.ts`)

- **Source:** `src/pwa/drafts.ts` — thin **`idb-keyval`** wrapper: `getDraft` / `setDraft` / `deleteDraft`, `makeChatDraftKey(threadId)`, `makeCaptureDraftKey(captureId)` (key helper for **future** QuickCapture; **no** capture UI in this pass).
- **Payload:** **plain text only** (max 32&nbsp;768 chars, trimmed; empty/whitespace deletes the row). **No** auth tokens, user ids in keys, assistant text, API responses, or transcript blobs.
- **Chat UI:** `ChatPage.tsx` loads/saves the composer per thread, debounced writes, clears the draft only after a **completed** assistant stream; **`threadStore`** + **`sessionStorage`** transcript mirroring is unchanged and **stays per-tab only** — not promoted to IndexedDB.
- **Out of scope here:** service worker / Cache Storage behaviour beyond 3B, Web Push, capture upload.

## Phase 3D — bounded TanStack Query persistence (`queryPersistence.ts`)

- **Purpose:** **`PersistQueryClientProvider`** saves a **filtered** dehydrated query subset to **`localStorage`** under **`lumogis:query-cache`**, **`maxAge`** 24&nbsp;h, **`buster`** = root **`package.json` `version`** (clears persisted client on release bump).
- **Allowlist:** `PERSISTABLE_QUERY_KEY_PREFIXES` — tuple-prefix allowlist (**empty at ship**). Only matching **successful** queries dehydrate; **mutations never** persist (`shouldDehydrateMutation: () => false`). Unknown keys—including **`['auth','me']`**, **`['admin',…]`**, **`['me',…]`**, **`['mcp',…]`**, **`['cc',…]`**—are excluded.
- **Runtime:** Persistence is **off** under Vitest (`import.meta.env.MODE === 'test'`) when `localStorage` is unavailable (soft degrade).
- **Not offline Lumogis:** the service worker remains **static precache only**. **Composer drafts** use **`drafts.ts`** (**3C**) separately.

## Phase 3E — offline status strip + reconnect alignment (`useOnlineStatus.ts`)

- **Hook:** `navigator.onLine` + `window` `online`/`offline` events; updates TanStack **`onlineManager`** (conservative refetch on reconnect — **no** `resumePausedMutations`, **no** offline mutation queue).
- **Banner:** `OfflineBanner` under the shell header when offline — **`role="status"`**, **`aria-live="polite"`**; states that **draft** text may remain locally (**3C**) but **chat / search / approvals / admin** need a connection (not “offline Lumogis”).
- **Chat:** send button disabled while offline; **no** auto-send on reconnect.
- **Phase 3** (3A–3E) is the **installable shell + static precache + bounded client storage + status UX** slice — **not** full offline orchestrator use. Parent plan **Phase 4** (Web Push), **Phase 5** (capture), **Phase 6** (Tauri) remain separate.

## Phase 4C — Web Push opt-in (client)

- **UI:** `src/features/me/PushOptIn.tsx` — mounted from **Settings → Notifications** (`MeNotificationsView.tsx`). **Only** on explicit button click: `Notification.requestPermission()` → `navigator.serviceWorker.ready` → `PushManager.subscribe` with `GET /api/v1/notifications/vapid-public-key` → `POST /api/v1/notifications/subscribe`. Lists redacted rows from `GET /api/v1/notifications/subscriptions` and updates prefs via `PATCH` / removes via `DELETE`.
- **Helpers:** `src/pwa/webPushBrowser.ts` (`urlBase64ToUint8Array`, `isWebPushSupported`, `createBrowserPushSubscription`) — **no** logging of endpoint or keys. `src/api/webPush.ts` wraps authenticated `ApiClient` calls.
- **Phase 4D — service worker UX:** **`src/pwa/swPush.ts`** (pure parsing/sanitization) + **`sw.ts`** — incoming encrypted push payloads become **`showNotification`**; **`notificationclick`** focuses an existing Lumogis tab or **`openWindow`** to the sanitized path. **Workbox** stays **precache-only** (**no `runtimeCaching`**). **`npm run verify:pwa-dist`** asserts **`dist/sw.js`** includes **`push`** / **`notificationclick`** and forbids **`runtimeCaching`** echoes.
- **Dev note:** `registerServiceWorker.ts` registers `/sw.js` only in **production** builds; local `vite dev` often has no controlling worker, so browser push enrolment may be unavailable until `npm run build` + `npm run preview` (HTTPS) or an installed PWA profile.

## Phase 4E — closeout (2026-04-29)

Operator env, HTTPS/Safari caveats, **`pywebpush`** image rebuild note, manual smoke checklist, **`registerRoute`** caveat, **`ACTION_EXECUTED`** defer (**FP-053**), and automated validation recorded in **`../../../../docs/architecture/cross-device-web-phase-4-web-push-plan.md#phase-4e-closeout`** (§23).

## Deferred (later parent-plan phases)

Phase **6** — Tauri desktop checklist per `.cursor/plans/cross_device_lumogis_web.plan.md`. Parent **Pass 4.3** (LibreChat compose defaults) stays separate from **`swPush`**. **File / media** Web Share Target (**POST** multipart) remains deferred (needs SW **`fetch`** or unsafe silent handling).
