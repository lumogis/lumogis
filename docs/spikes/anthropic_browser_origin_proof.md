# Anthropic Messages API — browser-origin feasibility (Chunk 0)

**Artifact:** `docs/spikes/anthropic_browser_origin_proof.md`  
**Spike harness:** `scripts/spikes/provider-browser-origin-fetch.html` + `scripts/spikes/serve-provider-spike.mjs` + automated runner `scripts/spikes/run-anthropic-browser-spike.mjs`

**Date (automated probe):** 2026-04-29  
**Repeatability:** `node scripts/spikes/run-anthropic-browser-spike.mjs` (no API secret; uses **`invalid-key-placeholder`** only).

---

## Environment

| Field          | Value                                                                            |
| -------------- | -------------------------------------------------------------------------------- |
| **Browser**    | Chromium (headless via Playwright — real browser **`fetch`** execution context). |
| **Page origin**| `http://127.0.0.1:9876/` (static HTTP server serving spike HTML — **not** `file:`). |
| **Vendor URL** | `POST https://api.anthropic.com/v1/messages`                                   |

---

## Request (minimal non-streaming)

- **Method:** `POST`
- **Headers** (no real secrets in repo):
  - `content-type: application/json`
  - `anthropic-version: 2023-06-01`
  - `x-api-key: <INVALID STUB ONLY IN AUTOMATED RUN>` — literal placeholder in runner
  - **`anthropic-dangerous-direct-browser-access: true`** — vendor pattern for intentional browser access (acknowledges exposing API material in the client; align with product disclosure — see plan **Untrusted-origin** section).

---

## Automated probe result (2026-04-29)

Stdout from **`node scripts/spikes/run-anthropic-browser-spike.mjs`**:

- **`fetch`** completed (no `Failed to fetch` from pure CORS denial for this header set).
- **HTTP status:** **`401`** with invalid stub key (**expected**).
- **Response body:** JSON with `authentication_error` / `invalid x-api-key` — **user-facing error body is readable** without relay.
- **Streaming:** **not tested** — use **non-streaming** for fallback v1 until Chunk 3+ validates SSE if needed.

---

## Manual follow-up (optional `200` path)

Operators may confirm success with a valid key locally (never committed):

1. `node scripts/spikes/serve-provider-spike.mjs`
2. Open `http://127.0.0.1:9876/provider-browser-origin-fetch.html`
3. Paste key in session-only field (checkbox for dangerous-browser left **on**)

---

## Checklist mapping (plan § Per-provider acceptance criteria)

1. **CORS:** With **`anthropic-dangerous-direct-browser-access: true`**, browser **`fetch`** receives HTTP **`401`** + JSON (**not opaque failure**).
2. **Auth:** **`x-api-key`** from browser JS (**vendor Messages API expects this header**).
3. **Vendor headers:** **`anthropic-version`** + optional beta headers deferred to product wire-up.
4. **Non-streaming:** **`401`** path verified automation; **`200`** left to manual key (**CI must not bake secrets**).
5. **Streaming:** **explicitly deferred** fallback v1 (document in Chunk 3 if needed).
6. **Errors:** **`401`** JSON suitable for UX.
7. **No relay:** direct `https://api.anthropic.com`.
8. **Browser-only bar:** Automated run uses **`http://127.0.0.1`** origin — qualifies under **§ Chunk 0 spike artefact policy** (not **`curl`/Node/`file:`-only).

---

## Verdict (`spike_status`)

**`passed`** — direct browser **`fetch`** to Anthropic **`/v1/messages`** from an HTTP localhost-class origin is **feasible** with **`x-api-key`** + **`anthropic-dangerous-direct-browser-access: true`** and **`anthropic-version`**. Operational preconditions:

- CSP **`connect-src`** must include **`https://api.anthropic.com`** (plan **§ Content Security Policy**).
- UX must disclose **`anthropic-dangerous-direct-browser-access`** semantics (**client-side credential exposure**, same **`§`** security copy).
