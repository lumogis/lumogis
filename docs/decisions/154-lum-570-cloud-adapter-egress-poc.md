# ADR-154: PoC proof harness for cloud-adapter egress hooks in FastAPI worker threads (LUM-570)

**Status:** Finalised

**Created:** 2026-07-05

**Last updated:** 2026-07-05

**Decided by:** /explore --headless (claude-opus-4.8, LUM-570)

**Finalised by:** /verify-plan 2026-07-05 (Composer, headless)

**Plan:** `.cursor/plans/LUM-570-cloud-adapter-egress-poc.plan.md`

**Exploration:** `.cursor/explorations/LUM-570-cloud-adapter-egress-poc.md`

**Draft mirror:** `.cursor/adrs/LUM-570-cloud-adapter-egress-poc.md`

**Linear:** [LUM-570](https://linear.app/lumogis/issue/LUM-570/p1-poc-cloud-adapter-socket-hooks-in-fastapi-worker-threads)

**Parent:** [LUM-553](https://linear.app/lumogis/issue/LUM-553/p2-tethered-in-process-egress-allowlist-defense-in-depth) — tethered egress allowlist (**ADR 153**)

## Context

LUM-553 / ADR 153 shipped an opt-in, default-off in-process egress allowlist (`LUMOGIS_FF_EGRESS_GUARD`) that wraps cloud-LLM adapter calls in `tethered.scope()`. Verify-plan left a **P1 gap** (plan Pass 0): the shipped tests exercise raw `tethered.scope()` sockets, allowlist building, and **mocked** route handlers — but never the **real** `OpenAILLM` adapter socket I/O through the production chain in a sync FastAPI **worker thread**, nor `chat_stream` generator resumption, nor httpx connection-pool behaviour. ADR 153 records this PoC as a revisit condition before public-release confidence.

## Decision

Prove the guard with **Option 1 — a local, offline fake endpoint driven through the real FastAPI route** (`TestClient` → sync route → anyio worker threadpool → real `OpenAILLM` → SDK socket), with `LUMOGIS_FF_EGRESS_GUARD=true`:

1. **Allowed path:** plain-HTTP fake server on `127.0.0.1:0`; natural `build_allowlist` + `privacy_mode=allow_cloud` → HTTP **200** with fake assistant content.
2. **Blocked path (non-stream):** `proxy_url` → `http://93.184.216.34/v1` (non-loopback; tethered blocks at `connect` with no outbound packets) + narrow `build_allowlist` excluding that host → HTTP **503** `egress_blocked`.
3. **Streaming second connect:** `tools: true` tool-loop — round 1 on allowlisted loopback fake server; round 2 repoints adapter client to off-list host → HTTP **200** SSE in-band `"egress guard"`.
4. **Cache eviction:** `invalidate_llm_cache_for_user("alice")` drops `llm:alice:chatgpt` from `config._instances` after warm call.
5. **Warm-pool diagnostic:** recording audit hook documents httpx keep-alive may skip a second `socket.connect` — residual limitation, not a privacy-toggle proof.

Deliverable is **test code + ADR 153 revisit note** — no production code changes.

**Option 2** (direct wrapped-adapter in spawned thread) was not required — route harness proved sufficient.

## Alternatives Considered

- **Option 2 — direct wrapped-adapter call in a spawned worker thread:** lower fidelity; fallback only — not needed.
- **Option 3 — observer audit hook alone:** used as embedded diagnostic only; enforcement remains HTTP/SSE assertions.
- **Option 4 — live external host / real cloud API:** rejected — offline/privacy CI posture.

Full comparison: `.cursor/explorations/LUM-570-cloud-adapter-egress-poc.md`.

## Consequences

- **Easier:** genuine, offline, repeatable evidence that the shipped guard observes real `OpenAILLM` egress across sync non-stream, streaming tool-loop reconnect, and cache invalidation; **partially closes** ADR 153 revisit condition.
- **Harder / foreclosed:** commits proof to a fake-endpoint harness; green result is evidence of *these paths*, not absolute isolation (guard remains bypassable per ADR 153).
- **Production hardening (same branch, pre-merge):** SDK `__cause__` unwrapping in `EgressGuardingLLMProvider`; `allow_localhost=False` on scoped calls; admin/per-user cache eviction closes httpx clients and drops `llm:` keys.
- **Documented limitation:** httpx keep-alive may skip a second `socket.connect` when the allowlist is unchanged — not a block bypass; privacy/credential changes evict cached adapters.

## Revisit conditions

- If `anthropic`/`openai` SDKs move off CPython sockets, PEP 578 audit path no longer applies — revisit ADR 153.
- Compose e2e (LUM-572) may supersede in-process harness as sign-off gate for operators.

## Status history

- 2026-07-05: Residuals closed pre-merge — production SDK unwrap, `allow_localhost=False`, cache eviction closes httpx pools; ADR 153 revisit **closed** for P1 PoC scope.
- 2026-07-05: Finalised by /verify-plan — `orchestrator/tests/test_egress_guard_poc.py` (8 tests) green with egress suite (40/40); ADR 153 status history updated; revisit condition **partially closed**.
- 2026-07-05: Draft created by /explore --headless (LUM-570).
