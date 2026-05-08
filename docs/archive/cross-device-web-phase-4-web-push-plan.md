---
status: extraction
implemented: 2026-04-29
test_result: passing (compose-test 1573/0; phased Web Push scoped 45; web 211/0)
verified_artefact: docs/architecture/cross-device-web-phase-4-web-push-plan.md
---

<!-- EXTRACTION VERIFY — Phase 4E closeout 2026-04-29 -->
## Implementation Summary (Phase 4 programme)

| | |
|---|---|
| **Phase 4 status** | **Closed** — slices **4A–4E** documented and automated validation executed |
| **Verified** | 2026-04-29 |
| **Automated tests** | **`make compose-test`** **1573**/9 skip/0 fail **(2026-04-29 `/verify-plan`)**; scoped pytest **41+4** (Web Push/OpenAPI/export) **+ test_no_raw_user_id_filter gate** OK; **`clients/lumogis-web`** **`npm test` 211**/0 + **`lint`** + **`build`** + **`verify:pwa-dist`** |
| **Manual Web Push smoke** | **Checklist supplied in §23.8** — **not executed** in headless/agent environment (**§23 caveats**) |
| **ADR** | **`docs/decisions/030-cross-device-client-architecture.md`** (finalised) |

**What shipped**

End-to-end **browser Web Push** for Lumogis Web: **`pywebpush`** + **VAPID** on server; **`PushOptIn`** (**gesture-only** **`Notification.requestPermission`**); **redacted** subscription list + prefs; service worker **`push`** / **`notificationclick`** with **minimal JSON** and **same-origin** URL allowlist; **`runtimeCaching`** still **absent**. **ntfy** unchanged. **`ACTION_EXECUTED` → push** deferred (**FP-053**). **Production:** rebuild orchestrator image so **`requirements.txt`** ( **`pywebpush`** ) is present.

**Caveats**

Bundled **`dist/sw.js`** may contain the string **`registerRoute`** inside **Workbox** internals — **not** a Lumogis **`runtimeCaching`** regression; guards are **`vite`** config (**no **`runtimeCaching`**)**, app **`sw.ts`** (no **`fetch`/API caches**), **`verify:pwa-dist`**.

<!-- END EXTRACTION VERIFY -->
# Cross-device Web — Phase 4 Web Push (implementation plan)

**Slug:** Parent topic `cross_device_lumogis_web` — Phase **4** (Web Push).

**Date:** 2026-04-29

**Kind:** Strategic **architecture / extraction** from *(maintainer-local only; not part of the tracked repository)*; **updated in-place** as Phase **4** chunks land (Phase **4A** backend update **2026-04-29**). **Not** an ADR.

**Roadmap position:** Phase **2** (mobile UX) and Phase **3** (PWA + bounded caching) are **closed**. **Mobile Cloud Fallback + Sync** (child plan **Chunk 0** planning/spike only; Chunks **1–5** parked) is **orthogonal** — do **not** conflate CSP/device-store crypto with Web Push/VAPID; only respect shared themes (credential-adjacent device handles omitted from portable export).

**Related:** `docs/decisions/030-cross-device-client-architecture.md` — umbrella ADR finalised Phase 4; *(maintainer-local only; not part of the tracked repository)* (mirror); `clients/lumogis-web/src/pwa/README.md` — Phase 3/4 caching boundary.

---

## Executive summary

**Phase 4** delivers **browser Web Push** for Lumogis Web using the **standard Web Push Protocol** (HTTPS + **`PushManager.subscribe`** + **`pywebpush`** on server) with **`VAPID`** keys configured per deployment. Per-user **`ntfy`** notifications remain a **parallel channel**: they reuse the **`Notifier`** port (`orchestrator/ports/notifier.py` → **`NtfyNotifier`**); Phase 4 **does not replace** ntfy. **Push is not** a shortcut around auth: payloads are minimal; recipients open **the authenticated app** for sensitive content.

**Already on `main`** (Phase 0): Postgres table **`webpush_subscriptions`**, CRUD-aligned REST routes under **`/api/v1/notifications/`**, **`GET /api/v1/me/notifications`** façade including synthetic **`web_push`** channel counts, **`user_export`** omission row for **`webpush_subscriptions`**, and orchestrator/API tests covering unauthenticated misuse and subscribe idempotency.

**Phase 4A shipped (2026-04-29):** **`orchestrator/services/webpush.py`** — **`pywebpush`** + VAPID private key **`WEBPUSH_VAPID_PRIVATE_KEY`** + subject **`WEBPUSH_VAPID_SUBJECT`** for outbound sends; **`ROUTINE_ELEVATION_READY`** hook only (generic body **Approval required**, **`/approvals`**); **`ACTION_EXECUTED`** **explicitly deferred** (hook carries connector/action identifiers — needs a **future safe template contract** before any generic wire-up); **`notify_on_signals` / `notify_on_shared_scope`** reserved for future **signal** pushes — ignored for approval template today; prune **404/410** subscriptions; **`GET /api/v1/notifications/test`** (**`WEBPUSH_DEV_ECHO`**) invokes real sender (**Test from Lumogis**). **ntfy** notifier path untouched.

**Production note:** **`pywebpush`** is declared in **`orchestrator/requirements.txt`**; rebuild or reinstall dependencies in orchestrator container images — **do not** rely on **ad-hoc** **`pip install`** in running containers.

**Phase 4B shipped (2026-04-29):** **`GET /api/v1/notifications/subscriptions`** returns a credential-safe list (**`endpoint_origin`** = scheme + host only — **no** full **`endpoint`** URL, **no** **`p256dh`**, **`auth`**, subscription JSON, or VAPID private material); **`PATCH /api/v1/notifications/subscriptions/{subscription_id}`** updates **`notify_on_signals`** and **`notify_on_shared_scope`** (unknown body fields **`422`**; empty body **`422`** via “at least one field” validation); **`POST /subscribe`** optionally accepts those two booleans (default insert semantics **`false`** / **`true`** when omitted — same as DDL; idempotent re-subscribe uses **`COALESCE`** so omitted prefs preserve existing columns). **`user_export`** unchanged — **`webpush_subscriptions`** remains omitted (device-bound handles). **`ACTION_EXECUTED`** remains **deferred**.

**Phase 4C shipped (2026-04-29):** Lumogis Web **`PushOptIn`** (`clients/lumogis-web/src/features/me/PushOptIn.tsx`) on **Settings → Notifications** — **`Notification.requestPermission()`** only after explicit user click (**never on mount**); **`navigator.serviceWorker.ready`** → **`PushManager.subscribe`** with VAPID from **`GET /vapid-public-key`**; **`POST /subscribe`**; redacted **`GET /subscriptions`**, **`PATCH`** prefs (**`notify_on_signals`**, **`notify_on_shared_scope`**), **`DELETE`**; helpers **`src/api/webPush.ts`**, **`src/pwa/webPushBrowser.ts`**. **runtimeCaching** and API **`Cache`** policy unchanged (**Phase 3** boundary).

**Phase 4D shipped (2026-04-29):** **`src/pwa/sw.ts`** **`push`** + **`notificationclick`** — minimal JSON (**`title`**, **`body`**, **`url`**, optional **`tag`**) via **`src/pwa/swPush.ts`** — generic defaults, length caps, allowlisted **`url`** paths only (**/**, **`/chat`**, **`/approvals`**, **`/me/notifications`**); **`showNotification`** with **`/icons/icon-192.png`**; clicks **focus** an existing same-origin window (with **`navigate`** when supported) or **`clients.openWindow`** — **no** fetch, **no** Cache Storage / **`IndexedDB`** in handlers; **`runtimeCaching`** remains **absent**.

**Phase 4E complete (2026-04-29):** Operator runbook, environment/troubleshooting, Workbox **`registerRoute`** string caveat, HTTPS/browser requirements, **automated validation record**, and **manual smoke checklist** (§21) — **`ACTION_EXECUTED`** remains **deferred**; **parent plan** **Pass 4.3 (LibreChat compose profile)** remains a **separate product decision** (not required to close the Web Push slice). **Mobile Cloud Fallback + Sync** stays **parked** after Chunk 0.

**Recommended sequence for other teams:** **Phase 5+** per parent plan (Phase **5** capture, Phase **6** Tauri stub). Web Push operations → §23 operator runbook. **Mobile Cloud Fallback + Sync:** Chunk **0** spike **parked** — unchanged by Phase 4.

---

## 1. What Phase 4 builds (product outcome)

When complete, Phase 4 lets a signed-in Lumogis Web user:

1. **Explicitly enable** browser push (**user gesture**, then **`Notification.requestPermission()`** — **never on page load**).
2. **Register one subscription per browser profile** via **`PushManager.subscribe`** with **`applicationServerKey`** from **`GET /api/v1/notifications/vapid-public-key`**.
3. **Persist subscription** (**endpoint + applicationServerKey-derived keys**) server-side with **per-user isolation** (see §6).
4. **Receive concise, low-sensitivity** system notifications (**titles/bodies** like “Approval required”, “Lumogis has an update”) wired from **explicitly allowed server events** (§8); **never** chat bodies, credential material, memory snippets by default.

Out of Phase 4’s **behaviour guarantee**: **routing chat through push**, offline tool execution, **Push as a substitute for SSE**.

---

## 2. What “Web Push” means in Lumogis

**Lumogis Web Phase 4 = browser-native Web Push**:

| Layer | Role |
|--------|------|
| **Push API / `PushSubscription`** | Client obtains **`endpoint`** + **`keys.p256dh`** + **`keys.auth`** minted against the deployment origin and the browser’s push bridge (FCM, Mozilla autopush, APNs for Web Push for Safari/WebKit where supported). |
| **Standard Web Push Encryption** | Encrypts payloads for the subscriber; server uses **`pywebpush`** (+ VAPID JWT for server authentication to the push service). |
| **Notification API (`ServiceWorkerRegistration.showNotification`)** | **Required** UX surface: user-visible notices are **`showNotification`** inside the **`push`** event handler (often with decrypted payload truncated or generic). **`push` alone** does not suffice for UX on most platforms. |
| **VAPID** | **`WEBPUSH_VAPID_PUBLIC_KEY`** / **`WEBPUSH_VAPID_PRIVATE_KEY`** + **`WEBPUSH_VAPID_SUBJECT`** (`mailto:` or `https:` contact). Public key exposes **`GET …/vapid-public-key`**. |

**Separate product channel — ntfy:** **`NtfyNotifier`** posts to configured ntfy topics per **`user_id`** (ADR **018**/022 trajectory). Households might use **ntfy on self-hosted infra** and optionally **browser push** — both can fire for the same semantic event later; policy must dedupe wording if both are noisy (Open questions §21).

---

## 3. Web Push vs ntfy — both?

**Both**, as **orthogonal delivery mechanisms**:

| Path | Adapter | Persistence | Today |
|------|---------|--------------|-------|
| **Signals / notifier path** | `config.get_notifier()` → **`NtfyNotifier`** (`orchestrator/adapters/ntfy_notifier.py`) | ntfy HTTP POST per user config | ✅ Used by **`signal_processor._notify`** when relevance threshold exceeded |
| **Web Push path** | New **`services/webpush.py`** (or **`web_push.py`** — match repo naming) | **`webpush_subscriptions`** Postgres | ⏭️ Phase 4 — **additive** subscribers on hooks (**not** a second `Notifier` implementation unless you unify behind a façade later) |

**Integration rule:** New hook handlers call **`send_web_push_safe(user_id, template=…)`** with **canonical short strings** — **never** funnel raw notifier title/message blobs that might contain scraped content into Web Push blindly.

---

## 4. Where subscriptions live

| Store | Detail |
|--------|--------|
| **PostgreSQL table** | **`webpush_subscriptions`** (`postgres/migrations/019-lumogis-web.sql`) |
| **Per row** | **One browser subscription** keyed by **`(user_id, endpoint)`** unique |
| **Columns (shipped)** | `id` **BIGSERIAL**, `user_id` **FK → users**, `endpoint`, **`p256dh`**, **`auth`**, **`user_agent`**, `created_at`, `last_seen_at`, **`last_error`**, **`notify_on_signals`** (bool, default **`false`**), **`notify_on_shared_scope`** (bool, default **`true`**) |

**Naming note:** Some external sketches use **user_push_subscriptions**; **`webpush_subscriptions`** is the shipped table name — keep it to avoid needless migration churn.

---

## 5. Subscription credentials & per-user isolation

* **Isolation:** Every query **filters by `authenticated user_id`** from JWT (`routes/api_v1/notifications.py` uses **`get_user(request).user_id`**). **Alice cannot list, PATCH, DELETE, or superscribe bob’s subscriptions** — covered by **`WHERE user_id = %s`** (**wrong-user PATCH/DELETE → 404**) and tests should assert cross-user patterns.
* **Treat `endpoint`/`p256dh`/`auth` as secrets:** **Do not** log full endpoint or key values; **`GET /api/v1/me/notifications`** only exposes **`subscription_count`**, never raw URLs.
* **`last_error`:** Intended for bounded operator-facing diagnostics (**length-capped**, no payload echo).

Optional future enhancements (prefer **minimal schema migrations**):

* **`failure_count`** / **`last_failure_at`** / **`last_success_at`** for prune heuristics
* **`enabled`** boolean if soft-disable without DELETE

**Revocation semantics (§6):** **DELETE subscription** removes the row (hard revoke). Optional **`PATCH` enable=false** deferred unless UX needs “pause” without re-subscribing — see §21.

---

## 6. Revocation / unsubscribe / “enabled” states

**Shipped:**

* **`GET /api/v1/notifications/subscriptions`** — **`200`** list of **`WebPushSubscriptionRedacted`** rows (origin-only endpoint summary + prefs + timestamps; **never** **`p256dh` / `auth` / full URL**).
* **`PATCH /api/v1/notifications/subscriptions/{subscription_id}`** — **`200`** returning the same redacted DTO (**`404`** if wrong **`user_id`** or unknown id — no existence leak).

* **`DELETE /api/v1/notifications/subscriptions/{subscription_id}`** — **`204`** when the row existed for caller; **`404`** if wrong id or wrong user.

**Implied:**

* **Push service `410 Gone` / invalid endpoint** → **delete row** in **`prune_invalid`** (sender path).

**Not shipped:**

* **Soft revoke (`revoked_at`)** column — absent; use **DELETE** + optional later migration if audit trail required.

---

## 7. Service worker & Phase 3 caching boundary

**Frozen from Phase 3** (`clients/lumogis-web/src/pwa/sw.ts`):

* Workbox **`precacheAndRoute(self.__WB_MANIFEST)`**, **`cleanupOutdatedCaches()`**, **`skipWaiting`/`clientsClaim`**
* **`runtimeCaching`** **absent** — **must stay absent** post–Phase 4

**Allowed Phase 4 additions** (additive listeners only):

* ✅ **Chunk 4D (2026-04-29):** **`push`** listener — parses minimal JSON (**`showNotification`**; defaults on parse failure — **never** echoed secrets); **`notificationclick`** — **`focus`/`navigate`** or **`clients.openWindow`** on **same-origin allowlisted paths** only (**`/`**, **`/chat`**, **`/approvals`**, **`/me/notifications`**) — no query strings carried (stripped).

**Forbidden:**

* **`registerRoute`** to **`/api/*`**, **`/events`**, or any orchestrator-backed URL
* **Cache Storage** persistence of JWT, export blobs, **`Notification.payload` Secrets**, HTML from authenticated navigations
* **Background sync** for tools or chat

**Payload rule:** Treat push payload as **operator-visible** (push services, browser vendors). **Default body** = generic label; **deep link** opens app over HTTPS with normal session.

---

## 8. Events **allowed** to trigger Web Push (initial policy)

When **`pywebpush`** send exists, **first wave** should focus on **high-signal, low-PII** actions:

| Event / source | Rationale | Suggested copy |
|----------------|-----------|----------------|
| **`ROUTINE_ELEVATION_READY`** (`permissions.py` / hooks) | User-actionable approval | “Approval required” / “Routine ready for review” |
| **`ACTION_EXECUTED`** (subset) | Only when **high-level** non-sensitive summary exists (e.g. routine completed) | “Routine completed” / “Action finished” — **no** tool args |
| **Connector / signal** (optional) | Only if **`notify_on_signals`** true for subscription **and** redacted title | “Lumogis has an update” |
| **Approvals queue** (derived) | **Mirror** pending approvals list — **count-only** or binary | “Approval required” |

**Preference:** drive off **existing `hooks.register(Event.…)`** patterns (`orchestrator/routes/events.py` registers **`ACTION_EXECUTED`**, **`ROUTINE_ELEVATION_READY`** for SSE; push listeners should **not** duplicate massive SSE payload construction — call small **template** builder.

---

## 9. Events **disallowed** (default)

| Class | Example | Why |
|-------|---------|-----|
| **Raw chat / assistant content** | Chat stream tokens | Privacy + push is not a chat transport |
| **Memory / search snippets** | User notes, retrieved docs | Sensitive |
| **Credentials / API keys / tokens** | Any connector secret | Excludes by policy |
| **Admin / audit detail** | `audit_log` bodies | High sensitivity |
| **Connector raw payloads** | Webhook JSON | May contain PII |
| **Provider secrets** | LLM API responses | Never |

---

## 10. Admin / user settings exposure

**User / Me (`/me/notifications`):**

* **Already:** Read-only **`GET /api/v1/me/notifications`** — **ntfy** + **`web_push`** rows with **`subscription_count`**, **`push_service_configured`**, **`why_not_available`** explanations.
* **Phase 4 adds:** **`PushOptIn` / banner** (**gesture-triggered**) + optional **subscription list** (endpoint **host** summary only — **not full URL** unless truncated safe) via new **`GET /api/v1/notifications/subscriptions`** (recommended — **not shipped** yet).

Per-subscription prefs:

* **`notify_on_signals`**, **`notify_on_shared_scope`** exist in DDL but **subscribe** route does **not** yet write them → Phase 4 should **PATCH defaults** explicitly or expose toggles (**POST body** extensions or **`PATCH`** on subscription row).

**Me / Devices (optional):**

* Surface **registered browser targets** beside future multi-device JWT table — **reuse** Web Push listing if **`GET` list** added.

**Admin → Diagnostics:**

* **Read-only** aggregate: **`webpush_configured`** bool, **`count_subscriptions`** total (no endpoints). Mirrors **no-secret** ethos of **`admin_diagnostics`**.
* **Avoid** **`GET`** that returns subscription endpoints wholesale.
* **“Send test notification”**: extend **`WEBPUSH_DEV_ECHO`** path or admin-gated **`POST`** with **`X` rate limit — sends **fixture** (“Test from Lumogis”) — **never in prod unrestricted**.

---

## 11. Existing codebase inventory

### 11.1 Backend

| Item | Location / Notes |
|------|------------------|
| **Table `webpush_subscriptions`** | `postgres/migrations/019-lumogis-web.sql` |
| **Routes** | `orchestrator/routes/api_v1/notifications.py` — **`/api/v1/notifications/vapid-public-key`**, **`/subscribe`**, **`DELETE …/subscriptions/{id}`**, **`GET /test`** (dev) |
| **Me façade** | **`GET /api/v1/me/notifications`** — `services/me_notifications.py`; **`WEB_PUSH_CHANNEL_ID = web_push`** |
| **Exports** | `user_export.py` **`_OMITTED_USER_TABLES["webpush_subscriptions"]`** with documented rationale (**per-device endpoint replay risk**) |
| **SSE** | `routes/events.py` — **foreground** realtime; complements push |

### 11.2 Notifier vs Web Push

* **`Notifier` protocol** (`ports/notifier.py`) — **`ntfy`** implementation only today.
* **`signal_processor._notify`** — calls **`get_notifier().notify`** for **signals** exceeding relevance (**not Web Push yet**).

### 11.3 Client

| Item | Notes |
|------|------|
| **`MeNotificationsView.tsx`** | Read-only table; mentions **tokens** privacy — **still no Push opt-in UI** |
| **`sw.ts`** | Precache-only — **no** `push` handler |
| **Registration** | `registerServiceWorker.ts` prod-only (**unchanged Scope**) |

### 11.4 Tests (existing anchors)

| Test | Purpose |
|------|---------|
| `orchestrator/tests/test_api_v1_notifications.py` | VAPID 503 gates, subscribe idempotency **`201`/`200`**, **`DELETE`** |
| `orchestrator/tests/test_api_v1_me_notifications.py` | Channel ordering, **`web_push`** synthetic row |
| **`test_user_export_tables_exhaustive`** | **`webpush_subscriptions`** omission registry |

---

## 12. Proposed architecture (target)

```
Hooks (ROUTINE_ELEVATION_READY, filtered ACTION_EXECUTED, …)
    → orchestrator/services/web_push.py — build minimal payloadDTO
        → pywebpush per row in webpush_subscriptions (user scoped)
            → prune 410 / mark last_error / optional failure counters
parallel: Notifier.notify → NtfyNotifier (unchanged)
```

---

## 13. API routes (shipped vs recommended)

### 13.1 Shipped (preserve OpenAPI continuity)

| Method | Path | Behaviour |
|--------|------|-----------|
| **GET** | **`/api/v1/notifications/vapid-public-key`** | Public key for **`SubscribeOptions`** (**503** if unset) |
| **POST** | **`/api/v1/notifications/subscribe`** | Register / refresh subscription (**503** if VAPID incomplete) |
| **DELETE** | **`/api/v1/notifications/subscriptions/{subscription_id}`** | Unsubscribe (**404** mismatch) |

**Note:** Plan text elsewhere sometimes suggested **`/api/v1/me/push-*`** — **implementations should retain** **`/api/v1/notifications`** as canonical (**tests + openapi.snapshot.json**).

### 13.2 Recommended additions (Phase 4)

| Method | Path | Purpose |
|--------|------|---------|
| **GET** | **`/api/v1/notifications/subscriptions`** | **Non-secret** manifest: **id**, **created_at**, **last_seen_at**, **truncated endpoint host**, maybe **device_label** (**no** **`p256dh`/`auth`**) |
| **PATCH** | **`/api/v1/notifications/subscriptions/{id}`** | **`notify_on_signals`**, **`notify_on_shared_scope`**, future **`enabled`** |
| **POST** | **`/api/v1/me/push-settings`** (**optional**) | **Aliases** **`GET`** if grouping under **`/me`** is preferred UX-only — duplicate routes **avoid** unless needed |

Alternative **push-config:** **`GET /api/v1/notifications/vapid-public-key`** already covers key **or** nest **`{"public_key", "subject": "mailto:..."}`** if **`WEBPUSH_VAPID_SUBJECT`** is safe to expose (usually yes — **`mailto`** contact).

---

## 14. Security, privacy & operations

### 14.1 Payload & logging

* **Structured logs:** **`webpush_sent user_id=<uuid>`** **`subscription_id=<id>`** **`status=success|pruned|error`** — **omit** ciphertext/endpoint tails.
* **Metrics:** counters only.

### 14.2 Rate limits & abuse

* **Subscribe/unsubscribe POST/DELETE:** per-user throttle (prevent endpoint spam).
* **Admin test push:** **admin-only**, **low rate**.

### 14.3 CSRF / auth

Subroutes use **`require_user`** (Bearer/session per FastAPI deps). **`POST`** should align with **`require_same_origin`** only if cookie-only session pattern applies — mirror **`routes/me.py`** patterns Phase 4 implements.

---

## 15. Configuration (environment variables)

**Required for full Phase 4 operation:**

| Variable | Purpose |
|----------|---------|
| **`WEBPUSH_VAPID_PUBLIC_KEY`** | Base64url public key surfaced to SPA |
| **`WEBPUSH_VAPID_PRIVATE_KEY`** | Server signing (**secret**) |
| **`WEBPUSH_VAPID_SUBJECT`** | **`mailto:`** or **`https:`** contact for VAPID JWT claims (**public**) |

**Optional:**

| Variable | Purpose |
|----------|---------|
| **`WEBPUSH_DEV_ECHO`** | Enables **`GET /api/v1/notifications/test`** helper |

**Feature disabled behaviour:** Existing **`503`** **`webpush_not_configured`** surfaces — client **dims** Push CTA (`me_notifications.py` aligns messaging).

---

## 16. User export policy (**confirmed`)

**Excluded:** **`webpush_subscriptions`** (see **`services/user_export.py`**). **Reason:** Browser-minted **push endpoints** tied to deployment origin/device — exporting them risks **wrong-device** replay; users **re-register** push at the destination.

---

## 17. Dependency management

Add **`pywebpush`** (pin in **`requirements.txt`**) plus confirm **`cryptography`** already transitively satisfies **`http-ece`** prerequisites.

---

## 18. Test plan (**extensions**)

### 18.1 Backend

| Test | Requirement |
|------|--------------|
| **Isolation** | **Alice** **`DELETE`/subscribe** yields **403/404** for **Bob’s ids** (**expand** **`test_api_v1_notifications`** with second user JWT) |
| **Validation** | Reject malformed **JSON** (**endpoint** malformed) |
| **Prune** | **Mock** **`410`** from push → row removed |
| **Redaction** | Unit test **`build_web_notification_body`** forbids substring matches for memory/chat |
| **`user_export`** | Omits **`webpush_subscriptions`** (already enumerated — **no regression**) |
| **Missing VAPID** | **503** persists |

### 18.2 Client (**Vitest**)

| Test | Requirement |
|------|--------------|
| **No prompt on mount** | `PushOptIn` **does not call** **`Notification.requestPermission`** without click |
| **Denied** | graceful copy |
| **`subscribe`** | **`fetchMock`** **`POST`** success path |
| **SW** (`sw` unit/smoke optional) **—** **`push`** handler tests may require **`service-worker`** test harness |

### 18.3 E2E (**Playwright**)

| Limitation | Mitigation |
|------------|------------|
| Chromium **Push** infra may vary | **Manual** Chrome (Application → Push, Notifications) + documented checklist |
| Automated paths | **`page.grantPermissions({ name: 'notifications' })`** where supported — environment-specific |

Smoke path: **`npm run build` → preview over HTTPS**, register SW, **`WEBPUSH_DEV_ECHO`** or **real** **`pywebpush`** against **FCM** test credential.

---

## 19. Documentation deltas (when Phase 4 implements)

| File | Updates |
|------|---------|
| **`clients/lumogis-web/src/pwa/README.md`** | **`push`** + **`notificationclick`** sections; **explicit** **`runtimeCaching` still forbidden** |
| **`clients/lumogis-web/README.md`** | Enable Push prerequisites (**HTTPS**) |
| ***(maintainer-local only; not part of the tracked repository)*** | **`/ Phase 4` closure bullet** (**when verifying**) |

---

## 20. Implementation chunks (**4A–4E**) — revised after repo audit

Chunks replace naive “migrate first” (**019** landed **Phase 0**).

### Chunk 4A — Delivery core + hooks contract ✅ (2026-04-29)

| | |
|--|--|
| **Shipped** | **`orchestrator/services/webpush.py`**: **`WebPushSendResult`**, **`send_templates_to_user`**, **`build_web_push_payload`**, prune on **410/404**, **`ROUTINE_ELEVATION_READY`** → threaded fan-out (**not** SSE); **`main.py`** registers **`register_web_push_hooks()`** + **`shutdown_web_push_executor`**; **`GET /api/v1/notifications/test`** returns **`{sent, failed, pruned, skipped, disabled_reason}`**; **`pywebpush`** in **`requirements.txt`**. **`ACTION_EXECUTED`** **deferred** (payload carries human-identifiable **`connector`** / **`action_name`** unless re-scoped later). **`notify_*`** prefs **not applied** to approval template (documented — reserved for future **`SIGNAL_RECEIVED`** path). |
| **Tests** | **`orchestrator/tests/test_webpush_service.py`**; **`test_api_v1_notifications.py`** **`/test`** contract updated. |
| **Non-goals met** | **No client UI**, **no SW push handlers**, **no** **`runtimeCaching`**, **no** new migrations. |

### Chunk 4B — Subscription listing + PATCH prefs + parity tests ✅ (2026-04-29)

| | |
|--|--|
| **Shipped** | **`GET /api/v1/notifications/subscriptions`** (redacted **`endpoint_origin`**, **`notify_*`**, **`last_error`/`user_agent`** truncated server-side); **`PATCH …/subscriptions/{id}`** for **`notify_on_signals`** / **`notify_on_shared_scope`**; optional prefs on **`POST /subscribe`** with idempotent **`COALESCE`** semantics; **`test_api_v1_notifications`** alice/bob isolation + **`openapi.snapshot.json`** + **`REQUIRED_V1_PATHS`** |
| **Tests** | **`test_api_v1_notifications.py`** (list redaction, PATCH cross-user **`404`**, partial PATCH, **`422`** empty PATCH, subscribe optional prefs); **`test_api_v1_openapi_snapshot.py`** |
| **Non-goals met** | **No client opt-in UI**, **no SW** changes, **no** **`runtimeCaching`**, **no** new migrations; **`ACTION_EXECUTED`** still deferred |

### Chunk 4C — Client opt-in (`PushOptIn`) + Me surface ✅ (2026-04-29)

| | |
|--|--|
| **Shipped** | **`PushOptIn.tsx`** + **`MeNotificationsView.tsx`** integration; **`src/api/webPush.ts`**; **`src/pwa/webPushBrowser.ts`**; Vitests **`tests/features/me/PushOptIn.test.tsx`**, **`tests/pwa/webPushBrowser.test.ts`** — permission **gesture-only**; list shows **`endpoint_origin`** only (no **`p256dh`/`auth`**); prefs **`PATCH`** one field at a time; **no SW** `push`/`notificationclick` (4D). |
| **Non-goals met** | **No** **`runtimeCaching`**, **no** API response caching via SW, **ACTION_EXECUTED** still deferred |

### Chunk 4D — `sw.ts` push + notificationclick ✅ (2026-04-29)

| | |
|--|--|
| **Shipped** | **`push`**: `event.data.json()` → **`normalizePushPayloadFromJson`** → **`showNotification`** (icon/badge **`/icons/icon-192.png`**). **`notificationclick`**: close + **`sanitizeNotificationClickUrl`** → **`clients.matchAll`** → focus + **`navigate`** when available, else **`openWindow`**. Helpers: **`src/pwa/swPush.ts`**. Vitest **`tests/pwa/swPayload.test.ts`**; **`scripts/check-pwa-dist.mjs`** asserts listeners + **no** **`runtimeCaching`**. |
| **Non-goals met** | **No** backend sender edits; **no** **`runtimeCaching`** / API route **`registerRoute`**; Workbox **precache-only** unchanged; **`ACTION_EXECUTED`** still deferred |

### Chunk 4E — Operator runbook / validation / ADR ✅ (2026-04-29)

| | |
|--|--|
| **Shipped** | Extraction doc §**23** (**runbook**, **troubleshooting**, **Workbox caveat**, **`ACTION_EXECUTED` deferral** note); **`docs/decisions/030-cross-device-client-architecture.md`**; parent plan **Phase 4 Web Push** milestone marked **verified**; **follow-up FP-053** (**ACTION_EXECUTED** safe template); portfolio + topic index synced per **`/verify-plan`** contract |
| **Non-goals met** | **No** **`ACTION_EXECUTED`** wire-up; **no** **`runtimeCaching`**; **no** ntfy behaviour change; **LibreChat** compose default flip (**parent Pass 4.3**) **not** executed this pass |

The parent **`cross_device_lumogis_web`** plan historically bundled a **LibreChat** compose deprecation slice with Phase **4** — treat it as **orthogonal** unless stakeholders schedule it; Web Push acceptance does **not** require it.

### Chunk legacy label (LibreChat — deferred outside 4E Web Push closeout)

| | |
|--|--|
| **Scope (parent §Pass 4.3)** | Compose **`COMPOSE_PROFILES`** default, docs — coordinate separately |

---

## 21. Open questions

1. **Dual fire** (ntfy + Web Push simultaneously): Dedupe tier / user preference hierarchy?
2. **`notify_on_signals` default `false`:** confirm **opt-in checkbox** UX for first launch.
3. **List endpoint shape:** Enough **truncate** **`endpoint`** to **scheme+host** only?
4. **`WEBPUSH_SUBSCRIPTION_REGISTERED`** event from parent §Codebase — **implement** (**audit**) or skip as YAGNI?
5. **Safari/WebKit + iOS PWA:** Known platform limits — mirror **FAQ** (**push** support evolving; degrade gracefully **`me_notifications`** copy).
6. **Admin Diagnostics:** Separate **`GET /admin/diagnostics/web-push`** aggregator vs **`build_admin_diagnostics_response`** augment — **minimal** payload.

---

## 22. Recommendation

**Proceed** with chunked plan **above**: treat **subscriptions schema + façade** as **complete for v1 bootstrap** (**extend** minimally for observability prefs). Implement **sender + hooks + UX + SW handlers** behind **explicit product boundaries** (**§§7–10**).

**Implementation status:** ✅ **Phase 4A–4E** (Web Push slice **verified 2026-04-29** — §§7–23).

---

## 23. Phase 4E closeout — operator runbook & validation {#phase-4e-closeout}

### 23.1 Environment variables (orchestrator)

| Variable | Role |
|-----------|------|
| **`WEBPUSH_VAPID_PUBLIC_KEY`** | Public key surfaced to **`GET /api/v1/notifications/vapid-public-key`** (URL-safe encoding for **`PushManager`**). |
| **`WEBPUSH_VAPID_PRIVATE_KEY`** | Private key — **never log** — used by **`pywebpush`** to sign outbound sends. |
| **`WEBPUSH_VAPID_SUBJECT`** | VAPID “contact”; **`mailto:`** or **`https:`** — required for standards-compliant sends. |

When these are unset, Web Push endpoints return **`503`** / **`{"error":"webpush_not_configured"}`** (planned surface — still true for gated deploys).

| Optional | Role |
|-----------|------|
| **`WEBPUSH_DEV_ECHO`** | When **`1`/`true`**, enables **`GET /api/v1/notifications/test`** to invoke the sender (dev tooling — see route guard). |

**Production orchestrator:** **`pywebpush`** is pinned in **`orchestrator/requirements.txt`**. Operator must **rebuild or redeploy** the container image — **never** **`pip install`** ad hoc in a running prod container.

### 23.2 Secure context — HTTPS vs localhost

- **HTTPS** is required for **Push API** outside **`http://localhost`** / loopback (**browser policy** — not Lumogis-specific). Use **`npm run preview`** or Caddy-terminated TLS (`docker compose`).
- **`Notification.requestPermission()`** appears only behind **explicit UI click** in **`PushOptIn`** (never on mount).

### 23.3 End-to-end flow (happy path — operator-facing)

1. Set VAPID keys + subject → restart/rebuild orchestrator.
2. Open Lumogis Web as signed-in user on a **HTTPS** or **localhost** origin.
3. **Settings → Notifications** → enable browser push (grant permission).
4. Confirm a row appears ( **`endpoint_origin` only** ).
5. Toggle **`notify_on_signals`** (prefs **`PATCH`**).
6. (**Dev**) call **`GET /api/v1/notifications/test`** with **`WEBPUSH_DEV_ECHO`** enabled — expect **`Test from Lumogis`** style notification → click → app focuses/opens allowlisted route (**`/`, `/chat`, `/approvals`, `/me/notifications`**).
7. Delete subscription (**`DELETE`** / UI) → list empty.

Routine approval pushes (**`ROUTINE_ELEVATION_READY`**) arrive when backend hooks fire (generic copy — **`ACTION_EXECUTED`** still deferred).

### 23.4 Troubleshooting (symptom → check)

| Symptom | Check |
|---------|--------|
| **`503`** on **`/vapid-public-key`** | VAPID trio missing / misconfigured. |
| **`Notification`** permission stays **denied** | OS/browser blocked site notifications — unblock in browser settings. |
| **`Push`** unsupported | Browser/OS (see §21 Safari/iOS caveat); degraded UX acceptable |
| **`PushManager`** errors / enrolment silently fails **`vite dev` | SW only **`import.meta.env.PROD`** — **`npm run build` + `preview`** or installed PWA |
| No toast / no visible **`showNotification`** | SW must control page — **`registerServiceWorker`** prod-only gate |
| Push accepted but disappears later | **`410`**/**`404`** invalid subscription — server prune; re-enrol |
| Click opens **`/` only** always | Inspect **`sanitizeNotificationClickUrl`** + payload (**non-allowlisted routes fall back to **`/`**) |
| Duplicate noisy alerts | Parallel **ntfy** + Web Push (**§21 Q1**) — preference dedupe deferred |

### 23.5 Workbox **`registerRoute` string-in-bundle caveat**

Bundled **`dist/sw.js`** (Workbox internals) **may contain the substring** **`registerRoute`**. That is **not** a Lumogis **`vite-plugin-pwa` `runtimeCaching`** regression. **Real guards:** **`vite.config.ts`** has **no** **`runtimeCaching`**; **`clients/lumogis-web/src/pwa/sw.ts`** has **no** app **`fetch`** to **`/api/*`** inside handlers; **`verify:pwa-dist`** blocks **`runtimeCaching`** token and verifies **`push`** listeners.

### 23.6 `ACTION_EXECUTED` deferral rationale (explicit)

Hooks carry **connector / action identifiers** that are unsafe to mirror verbatim into notification bodies until a dedicated **sanitised template contract** ships — **follow-up **`FP-053`**; **no server wire-up in Phase 4**.

### 23.7 Automated validation results (Phase 4E — 2026-04-29)

**Full orchestrator suite (canonical — Docker, **`AUTH_ENABLED=false`** for TestClient parity):**

```bash
# repo root — matches Makefile compose-test (mounts `/project`, installs requirements + dev)
make compose-test
```

**Result (2026-04-29 `/verify-plan` re-pass):** **1573 passed**, **9 skipped**, **0 failed**.

Scoped Web Push + export sentinel (host **`cd orchestrator && .venv/bin/python`**):

```bash
.venv/bin/python -m pytest \
  tests/test_webpush_service.py \
  tests/test_api_v1_notifications.py \
  tests/test_api_v1_me_notifications.py \
  tests/test_api_v1_openapi_snapshot.py \
  tests/test_user_export_tables_exhaustive.py -q
```

**Result:** **45 passed**.

Bare **`make test`** / bare **`pytest`** on some hosts lacks **`pytest`** on **`PATH`** or picks up host **`AUTH_ENABLED`** — prefer **`make compose-test`** above for “all green”. Local venv **`pytest`** without **`AUTH_ENABLED=false`** can cascade failures (bootstrap / LibreChat path); **`compose-test`** is the authoritative gate.

**Lumogis Web:**

```bash
cd clients/lumogis-web && npm test && npm run lint && npm run verify:pwa-dist && npm run build
```

**Result:** **`npm test` 211**/0 **`lint`** clean **`verify:pwa-dist`** OK **`build`** OK.

(Warnings-only: FastAPI deprecation `regex` params; JWT test key length; pytest cache permission in some sandbox — none failed tests.)

**Acceptance #4 grep gate:** **`webpush`** list queries now carry **`# SCOPE-EXEMPT:`** on **`webpush_subscriptions`** **`WHERE user_id = %s`** reads (same rationale as **`POST /subscribe`** — **2026-04-29** **`/verify-plan`** fix).

### 23.8 Manual Web Push smoke status

The **manual checklist** in §23.3 was **not** executed inside this CI/agent environment (**no Chromium profile + no live push credential loop** here). Automated coverage plus this runbook is the Phase **4E** acceptance basis — **explicit caveat**.

---

<!-- VERIFY-PLAN: implementation-log extraction doc -->

## Implementation Log — `/verify-plan`

**Verified by:** Cursor agent  
**Date:** 2026-04-29  
**Plan artefact:** `docs/architecture/cross-device-web-phase-4-web-push-plan.md` (Phase **4A–4E** extraction — not *(maintainer-local only; not part of the tracked repository)*; parent roadmap remains *(maintainer-local only; not part of the tracked repository)*)

| | |
|---|---|
| **Critique rounds** | *N/A — verification pass on shipped Phase 4 + 4E closeout* |
| **`make compose-test`** | **1573** passed / **9** skipped / **0** failed |
| **Phase 4 scoped pytest** | **46** passed (Web Push/OpenAPI/export + **Acceptance #4** grep gate) |
| **`clients/lumogis-web`** | **211** Vitest **0** failed; **`lint`**, **`verify:pwa-dist`**, **`build`** OK |

### What matched the plan

1. Backend Web Push (**`pywebpush`**, VAPID, hooks, prune, **`/notifications/*`** isolation + redacted list).
2. Client **`PushOptIn`**, **`sw.ts`** **`push`** / **`notificationclick`**, **`swPush`** allowlist — **no** **`runtimeCaching`**.
3. Operator §**23** runbook + **ADR `030`**; **`ACTION_EXECUTED`** remains **deferred** (**FP-053**); manual smoke **documented**, **not** run in CI.

### Deviations (intent preserved)

1. **`# SCOPE-EXEMPT:`** comments added **2026-04-29** on **`webpush_subscriptions`** **`WHERE user_id = %s`** SELECTs in **`webpush.py`** and **`notifications.py`** — satisfies Acceptance **#4** grep gate (`personal_shared_system_memory_scopes` plan); semantic isolation unchanged (parameterised **`user_id`** only).

### Implementation errors

*None.*

### Critical violations

*None.*

### ADR notes

Umbrella ADR **`docs/decisions/030-cross-device-client-architecture.md`** finalised Phase **4 Web Push**; mirror ***(maintainer-local only; not part of the tracked repository)*** — unchanged this pass aside from extraction doc §**23**.

### Security findings

*None.*

### Test fixes applied

* **`tests/test_no_raw_user_id_filter_outside_admin.py`** required **`# SCOPE-EXEMPT:`** lookback markers on two Web Push **`SELECT`** sites — implemented in **`orchestrator/services/webpush.py`**, **`orchestrator/routes/api_v1/notifications.py`**.

### Potential regressions

*None observed — **`make compose-test`** green after marker fix.*

### Recommended next steps

1. Proceed with parent **Phase 5 / 6** when scheduled; **`FP-053`** if **`ACTION_EXECUTED`** push is productised.

---

## Appendix A — Boundary note (**Mobile Cloud Fallback**)

The fallback plan (***(maintainer-local only; not part of the tracked repository)***) repeats **credential-adjacency** omission patterns for hypothetical **cloud device** rows — align **privacy language** (**device handles non-portable**) but **share no wire format** **with Web Push.**

---

_End of extraction document._
