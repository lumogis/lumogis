# Launch demo — automated recording (LUM-181)

Records the two-user **household knowledge-base** flow as a clean, paced, jitter-free GIF — instead of hand-recording two browser windows (the thing that keeps failing with GPU "blue jitter" and mistimed clicks).

**The flow:** admin uploads a household doc → **shares it with the household** → a *second* account (member) **searches and finds it** → opens **document-chat** and asks → grounded answer with a citation.

## One-time setup
- A running stack: `docker compose up -d`.
- Two real accounts: the **admin** (smoke creds) and a **member** (create once via the invite flow — see `household_invite.spec.ts`).
- `ffmpeg` on your machine (for the GIF). Optional `gifski` for higher quality.

## Record it
```bash
cd clients/lumogis-web

export PLAYWRIGHT_BASE_URL=http://127.0.0.1
export LUMOGIS_WEB_SMOKE_EMAIL=admin@yourhome.lan   LUMOGIS_WEB_SMOKE_PASSWORD='…'   # admin
export DEMO_MEMBER_EMAIL=partner@yourhome.lan        DEMO_MEMBER_PASSWORD='…'         # member

npx playwright test -c playwright.demo.config.ts
```
This writes two `.webm` clips to `test-results/demo/video/` (admin scenes, then member scenes).

## Turn it into a GIF
```bash
./scripts/demo-to-gif.sh test-results/demo/video docs/assets/demo.gif
# higher quality:  USE_GIFSKI=1 ./scripts/demo-to-gif.sh …
# too big (>5 MB): FPS=10 WIDTH=960 ./scripts/demo-to-gif.sh …
```
Then reference `docs/assets/demo.gif` from the README and the site.

## Tuning
- **Pace:** `DEMO_SLOWMO_MS=350` (browser slowMo) + the `beat()` pauses in the spec. Raise for a calmer feel.
- **Shorter GIF:** pre-seed an already-ingested doc and skip Scene 1's upload/ingest wait (set `sharedDocId` directly).
- **Captions:** add short text overlays in `demo-to-gif.sh` with an ffmpeg `drawtext` filter per scene if you want labels.

## Why this is robust
- Reuses the **real selectors** already proven in `documents.spec.ts` (`share-toggle`, `documents-shared-filter`), `document_chat.spec.ts` (`context-used-strip`, the "Ask about this document…" box), and the `smoke-auth` login — so it tracks the real UI, not a mock.
- Drives the **real backend** (real ingest, share, member search, doc-chat) — it's a genuine proof of the flow *and* the demo asset in one run. If it records green, the two-user launch flow works.

> Not wired into CI (it needs a live stack + two accounts + video). It's a manual `make`-style recording tool. Consider a `web-demo` Makefile target wrapping the two commands above.
