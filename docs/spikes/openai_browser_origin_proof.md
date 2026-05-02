# OpenAI Responses/Chat API — browser-origin feasibility (Chunk 0)

**`spike_status` (matrix):** **`blocked_by_default`** *(not **`passed`** in Chunk 0)*  
**Date:** 2026-04-29

---

## Decision

Chunk 0 intentionally **does not** certify OpenAI into **`fallback_provider_policy`** (**Option B**: defer full **`passed`** spike).

### Rationale

- **Policy priority:** MVP targets **Anthropic** as the **`passed`** row (`docs/spikes/anthropic_browser_origin_proof.md`). OpenAI waits on a separate, explicit browser-origin artefact that completes **every** row in **§ Per-provider acceptance criteria**.
- **`curl`/Node-alone proofs remain non-qualifying** (**§ Chunk 0 spike artefact policy**).
- **`not_tested`** is disallowed for ship-listing (**plan gate**) — **`blocked_by_default`** communicates “no Lumogis-managed browser certification yet.”

### Supplementary probe (automated Chromium, stub key — **non-normative**)

Script `scripts/spikes/run-openai-browser-spike.mjs` (stub **`Bearer`** only) observed **`fetch`** completing with **`401`** JSON (`invalid_api_key`). This does **not** substitute for **`passed`** QA (streaming, OAuth, vendor header matrix, CSP alignment). **Treat as exploratory only.**
