# Launch demo — automated recording (LUM-181)

Records the two-user **household knowledge-base** flow as a clean, paced, jitter-free GIF — instead of hand-recording two browser windows (the thing that keeps failing with GPU "blue jitter" and mistimed clicks).

## Short demo (README / marketing) — `make web-demo-short`

**Use this for `branding/demo2.gif`.** Three scenes only (no upload wait when the fixture doc is already indexed), slower pacing, and **caption overlays** baked into the GIF plus matching step text in the root **README**.

| Scene | What happens |
| --- | --- |
| 1 | Admin shares `household-insurance.md` with the household |
| 2 | Member searches and finds the shared doc |
| 3 | Member asks in document-chat — grounded answer with citation |

```bash
docker compose up -d
export PLAYWRIGHT_BASE_URL=http://127.0.0.1
export LUMOGIS_WEB_SMOKE_EMAIL=… LUMOGIS_WEB_SMOKE_PASSWORD=…
export DEMO_MEMBER_EMAIL=… DEMO_MEMBER_PASSWORD=…
make web-demo-short
```

Tuning (short):

- **Pace:** `DEMO_SLOWMO_MS=650`, `DEMO_BEAT_MS=2800`, `DEMO_TYPE_DELAY_MS=95` (set in the Makefile target; override when invoking Playwright directly).
- **Captions:** edit `clients/lumogis-web/scripts/demo-short-captions.txt` (one line per scene).
- **GIF size:** `FPS=10 WIDTH=960` in `demo-to-gif-short.sh` defaults; lower if the asset exceeds ~5 MB.

## Full demo (release proof) — `make web-demo`

The original four-scene flow including **real upload → ingest → share → search → chat**. Use when you want a live proof of the whole pipeline, not for the README hero GIF.

**The flow:** admin uploads a household doc → **shares it with the household** → a *second* account (member) **searches and finds it** → opens **document-chat** and asks → grounded answer with a citation.

## One-time setup
- A running stack: `docker compose up -d`.
- Two real accounts: the **admin** (smoke creds) and a **member** (create once via the invite flow — see `household_invite.spec.ts`).
- `ffmpeg` on your machine (for the GIF). Optional `gifski` for higher quality.

## Record full demo manually
```bash
cd clients/lumogis-web

export PLAYWRIGHT_BASE_URL=http://127.0.0.1
export LUMOGIS_WEB_SMOKE_EMAIL=admin@yourhome.lan   LUMOGIS_WEB_SMOKE_PASSWORD='…'   # admin
export DEMO_MEMBER_EMAIL=partner@yourhome.lan        DEMO_MEMBER_PASSWORD='…'         # member

npx playwright test -c playwright.demo.config.ts
```
This writes two `.webm` clips to `test-results/demo/video/` (admin scenes, then member scenes).

## Turn full demo into a GIF
```bash
./scripts/demo-to-gif.sh test-results/demo/video docs/assets/demo.gif
# higher quality:  USE_GIFSKI=1 ./scripts/demo-to-gif.sh …
# too big (>5 MB): FPS=10 WIDTH=960 ./scripts/demo-to-gif.sh …
```

## Tuning (full)
- **Pace:** `DEMO_SLOWMO_MS=350` (browser slowMo) + the `beat()` pauses in the spec. Raise for a calmer feel.
- **Shorter GIF:** use `make web-demo-short` instead of trimming the full spec by hand.

## Why this is robust
- Reuses the **real selectors** already proven in `documents.spec.ts` (`share-toggle`, `documents-shared-filter`), `document_chat.spec.ts` (`context-used-strip`, the "Ask about this document…" box), and the `smoke-auth` login — so it tracks the real UI, not a mock.
- Drives the **real backend** (real ingest, share, member search, doc-chat) — it's a genuine proof of the flow *and* the demo asset in one run. If it records green, the two-user launch flow works.

> Not wired into CI (it needs a live stack + two accounts + video). It's a manual `make`-style recording tool.
