# Lumogis Web

Responsive PWA client for the Lumogis orchestrator. Phase 0 ships the build
scaffold and OpenAPI codegen plumbing; the feature pages (chat, search,
approvals, captures, notifications) land in Phases 1–5 per
`.cursor/plans/cross_device_lumogis_web.plan.md`.

Backend vocabulary for tools vs actions vs MCP vs capabilities:
[`../../docs/architecture/tool-vocabulary.md`](../../docs/architecture/tool-vocabulary.md).

**Settings → Tools & capabilities** (`/me/tools-capabilities`) shows a read-only list from
`GET /api/v1/me/tools` (diagnostics only; no tool execution in the UI).

**Settings → LLM providers** (`/me/llm-providers`) shows a read-only snapshot from
`GET /api/v1/me/llm-providers` (tier / configured status only — no API keys; edit keys under **Connectors**).

**Settings → Notifications** (`/me/notifications`) combines a read-only channel snapshot from `GET /api/v1/me/notifications` with **Phase 4C** browser Web Push enrolment (`PushOptIn`: VAPID probe, permission on button click only, `PushManager.subscribe`, redacted subscription list, `PATCH` prefs / `DELETE`) **and Phase 4D** — the production service worker shows system notifications (`push`) and routes clicks through `notificationclick` to same-origin paths only (**/**, **`/chat`**, **`/approvals`**, **`/me/notifications`**) via `src/pwa/swPush.ts`. ntfy credentials are edited under **Connectors**; no tokens or raw push keys are shown in the UI.

**Settings → Profile** (`/me/profile`) lets signed-in users change their password (`POST /api/v1/me/password`). Admins can reset another user’s password from **Admin → Users** (`POST /api/v1/admin/users/{user_id}/password`). There is no email-based forgot-password flow; shell recovery for operators is `python -m scripts.reset_password` from the `orchestrator` directory (see orchestrator script docstring).

**Admin → Users** also supports **per-user backup ZIP export** for any row (`POST /api/v1/me/export` with `target_user_id`) and **import from server-side archives** (`GET` / `POST /api/v1/admin/user-imports`): the UI lists ZIPs already on the orchestrator under the export directory, runs an optional dry-run preview, then creates a new account from the archive. Imports require a new email and initial password in the form (not read from the ZIP). The UI does not surface password hashes or other secrets from API responses. ZIPs include **Phase 5** capture metadata (**`captures`**, **`capture_attachments`**, **`capture_transcripts`**) and attachment binaries under **`captures/media/`** in the archive.

**QuickCapture** (`/capture`): mobile-friendly capture with local IndexedDB draft and outbox for queued sync when back online; the PWA **GET** share target only **prefills** title/text/url — nothing is saved until the user taps **Save** (or uses local save / Add to memory). This is still **not** “full offline Lumogis”: chat, search, and most admin flows need the live orchestrator (`navigator.onLine` / **`OfflineBanner`** describe the same limitation as Phase 3E).

**Admin → Diagnostics** (`/admin/diagnostics`, admin role only) shows a read-only snapshot from
`GET /api/v1/admin/diagnostics` (stores, capabilities, tool catalog summary — no secrets or destructive actions). Credential key rotation counts still come from `GET /api/v1/admin/diagnostics/credential-key-fingerprint`.

## Quick start

Local dev requires the orchestrator running at `http://localhost:8000`
(or set `LUMOGIS_DEV_ORCHESTRATOR_URL`). The Vite dev server proxies
`/api`, `/events`, `/v1`, and `/mcp` to it so the SPA stays same-origin.

```bash
npm install
npm run dev          # http://localhost:5173
npm run lint
npm run build        # static bundle in dist/
npm run codegen      # regenerate src/api/generated/openapi.d.ts from committed snapshot
npm run codegen -- --live   # generate from live orchestrator /openapi.json
# Drift check vs *live* API (orchestrator must be up; LUMOGIS_OPENAPI_URL overrides host, default :8000):
npm run codegen:check
```

## Production deployment

The production deployment serves the built `dist/` bundle via the nginx
container in this directory's `Dockerfile`, which is fronted by Caddy
(`../../docker/caddy/Caddyfile`) at the root path. Same-origin routing
keeps `/api/v1/*`, `/events`, `/v1/*`, and `/mcp/*` on the same host so
the refresh cookie's `SameSite=Strict` and `Path=/api/v1/auth` policies
behave correctly without CORS.

Bring the stack up (Caddy + `lumogis-web` + orchestrator are in the base
`docker-compose.yml` since Phase 1 Pass 1.5):

```bash
docker compose up -d --build
# Lumogis Web: http://localhost/   (or http://127.0.0.1/)
# Orchestrator direct: http://localhost:8000/
```

### Integration smoke + Playwright e2e

From the repo root, with the stack running and `AUTH_ENABLED=true` plus a
real user password (≥ 12 characters):

```bash
export LUMOGIS_WEB_BASE_URL=http://127.0.0.1
export LUMOGIS_WEB_SMOKE_EMAIL=you@example.com
export LUMOGIS_WEB_SMOKE_PASSWORD='your-password-here'
make test-integration   # includes tests/integration/test_lumogis_web_smoke.py
```

Playwright (Chromium) first-slice spec — install browsers once per machine:

```bash
cd clients/lumogis-web && npx playwright install chromium
export LUMOGIS_WEB_SMOKE_EMAIL=... LUMOGIS_WEB_SMOKE_PASSWORD='...'
make web-e2e                 # skips tests if creds missing (local-friendly)
make web-e2e-prove           # fails if creds missing — use in CI or release checks
```

Optional: `PLAYWRIGHT_BASE_URL=https://your.host` if not testing on port 80.

**Caddy security headers (repo root):** `make web-caddy-headers` with the stack up, or `make web-caddy-headers-prove` when the check must fail if Caddy is down.

## Lighthouse (mobile) — `/chat` (Phase 2D)

Target (parent **Phase 2** DoD, **mobile** emulation): **Performance ≥ 80**, **Best Practices ≥ 90** on **`/chat`**. **PWA** and **SEO** Lighthouse categories are **not** gates for this tranche.

Run against the **production build** and **Vite preview**, not `npm run dev`:

```bash
npm run build
npm run preview   # default http://127.0.0.1:4173 — keep this terminal open

# Other terminal (from this directory):
npm run lighthouse:chat
```

`lighthouse:chat` invokes **`npx lighthouse`** with `--form-factor=mobile` and `--only-categories=performance,best-practices`. A JSON report to inspect scores locally:

```bash
CHROME_PATH=/path/to/chrome-or-chromium \
  npx --yes lighthouse http://127.0.0.1:4173/chat \
  --form-factor=mobile \
  --only-categories=performance,best-practices \
  --output=json --output-path=./lighthouse-chat.json
```

**Chrome:** Lighthouse needs a recent **Chrome or Chromium** (see [chrome-launcher](https://github.com/GoogleChrome/chrome-launcher)). If the CLI reports no Chrome installation, set **`CHROME_PATH`** to your `google-chrome`, `chromium`, or a compatible binary. Playwright’s downloaded Chromium is **not** always compatible with Lighthouse’s protocol expectations — prefer a real Chrome stable where possible.

**CI:** A Lighthouse job is **not** mandatory in this repo by default (headless variance and runner image differences). Treat this as a **documented local or release** check unless you add an optional workflow with stable browser provisioning.

**Auth:** A cold load of `/chat` may show the **login shell** if the preview origin has no session; scores then describe that page. For “logged-in chat” budgets, extend the procedure (future) or test against a deployment where the shell is already authenticated.

## Phase 3 — PWA (3A manifest + 3B static SW + 3C drafts + 3D query infra + 3E offline UX)

**3A — metadata:** **`public/manifest.webmanifest`**, **`public/icons/*`**, and **`index.html`** manifest / theme-color / Apple web-app hints (see `tests/pwa/manifest.test.ts`). Icons: `python3 scripts/generate-pwa-icons.py` (requires Pillow).

**3B — static precache:** Workbox **`injectManifest`** emits **`dist/sw.js`**, registering from **`main.tsx`** **only** in production **`import.meta.env.PROD`**. Precache targets **built static assets only** (`precacheAndRoute`); **there is no `runtimeCaching`**, navigation fallback, or private API/offline semantics — **`/api/*`**, **`/events`**, **`/v1/*`**, and other orchestrator routes are **not** cached here. Detailed notes: **`src/pwa/README.md`**. Safety tests: `tests/pwa/serviceWorker.test.ts`.

**3C — IndexedDB composer drafts:** **`src/pwa/drafts.ts`** uses **`idb-keyval`** to persist **unsent chat input text only**, keyed by local thread id (`lumogis:draft:chat:<threadId>`). Drafts are debounced while typing, cleared after a **successful** assistant stream completes, and restored on failed/aborted sends. **Chat transcripts** stay **ephemeral per tab** in React + **`sessionStorage`** — they are **not** written to IndexedDB.

**3D — bounded TanStack Query persistence:** **`src/pwa/queryPersistence.ts`** wires **`PersistQueryClientProvider`** (when not under Vitest) with **`@tanstack/query-sync-storage-persister`** → **`localStorage`** key **`lumogis:query-cache`**, **`maxAge`** 24&nbsp;h, **`buster`** = **`package.json` `version`**. **`PERSISTABLE_QUERY_KEY_PREFIXES`** is an **explicit allowlist** (currently **empty** — no cached query payloads are written until a safe read-only key is added); `auth`/`me`/`admin`/MCP/connectors/me-page queries are **never** persisted by policy (`shouldDehydrateMutation` always false). This is **not** offline search/chat — orchestrator APIs still require network auth. **`drafts.ts`** persistence is separate.

**3E — offline status + reconnect hints (UX only):** **`src/pwa/useOnlineStatus.ts`** drives a shell **`OfflineBanner`** (under the header in **`AppShell`**) when `navigator` reports offline. Copy is explicit that support is **limited**: composer **draft** text may remain on device (**3C**); **chat, search, approvals,** and **admin** actions still require a connection. **`useOnlineStatus`** also calls **`onlineManager.setOnline`** from TanStack Query so reconnect refetch behaves conservatively (**no** mutation replay, **no** queued writes). **`ChatPage`** disables **Send** while offline. This is **not** “Lumogis works offline”; there is **no** background sync or private API **`runtimeCaching`**.

**Inspect (Chrome DevTools → Application):** **Manifest** (3A), **Service Workers** / **Cache Storage** (3B), **IndexedDB / Local Storage** as applicable (drafts / query infra). After **`npm run build`**, **`npm run verify:pwa-dist`** asserts **`dist/sw.js`** + manifest + icons exist (`npm run preview` to browse the **`dist`** output locally).

**Debugging / reset locally:** unregister the SW or clear site data (**Application → Service Workers** / **Storage**).

## Phase 4 — Web Push (operator runbook)

Phase **4A–4E** (**2026-04-29**): backend VAPID send + **`pywebpush`**, subscription API + isolation tests, **`/me/notifications`** opt-in (**gesture-only** permission), **`sw.ts`** **`push`** / **`notificationclick`** via **`swPush.ts`** (**no** **`runtimeCaching`**, **no** API response caching — **`verify:pwa-dist`** guards). **`ACTION_EXECUTED`→browser push** stays **deferred** (**follow-up** **FP-053**). **ntfy** remains a **parallel** channel. **Production:** rebuild/redeploy the **orchestrator** image after **`requirements.txt`** changes so **`pywebpush`** is installed.

Full checklist, env vars (`WEBPUSH_VAPID_*`, optional **`WEBPUSH_DEV_ECHO`**), troubleshooting, Workbox **`registerRoute`** string caveat, and validation log: **`../../docs/architecture/cross-device-web-phase-4-web-push-plan.md#phase-4e-closeout`**.

## OpenAPI snapshot

`openapi.snapshot.json` is the **deterministic** wire contract the SPA
codegens against. Regenerate after adding or changing any `/api/v1/*`
route:

```bash
cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys \
  --out ../clients/lumogis-web/openapi.snapshot.json
```

(Run from **repo root** so `cd orchestrator` and the `--out ../clients/...` path resolve; this matches `orchestrator/tests/test_api_v1_openapi_snapshot.py` and `orchestrator/scripts/dump_openapi.py`.)

`npm run codegen:check` (repo root: `make web-codegen-check`) fetches the live
`/openapi.json` and fails if the committed snapshot would change — requires a
**running** orchestrator at `LUMOGIS_OPENAPI_URL` (default
`http://localhost:8000/openapi.json`). Offline CI for this check remains a
future improvement (`openapi_check_offline_or_mock` in platform remediation docs).
