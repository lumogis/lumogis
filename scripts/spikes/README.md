Non-product artefacts for **`lumogis_mobile_cloud_fallback_sync`** Chunk 0. **Do not import** these into **`clients/lumogis-web/src`**.

| File | Purpose |
|------|---------|
| `serve-provider-spike.mjs` | Minimal static HTTP server (**`127.0.0.1`**) serving `provider-browser-origin-fetch.html`. |
| `provider-browser-origin-fetch.html` | Manual browser **`fetch`** to Anthropic/OpenAI (session-only keys). |
| `run-anthropic-browser-spike.mjs` | Headless Chromium (Playwright): **qualifying **`passed`** probe** (`401` stub, **`anthropic-dangerous-direct-browser-access: true`**). |
| `run-openai-browser-spike.mjs` | Exploratory only — **not matrix-qualifying **`passed`**. |

See **`../../../docs/spikes/`** for **`md`** proofs.
