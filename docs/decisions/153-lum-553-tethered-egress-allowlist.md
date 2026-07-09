# ADR-153: In-process egress allowlist as opt-in defense-in-depth (LUM-553)

**Status:** Finalised

**Created:** 2026-07-05

**Last updated:** 2026-07-05

**Decided by:** /explore --headless (claude-opus-4-8-thinking-medium, LUM-553)

**Finalised by:** /verify-plan 2026-07-05 (Composer)

**Plan:** `.cursor/plans/LUM-553-tethered-egress-allowlist.plan.md`

**Exploration:** `.cursor/explorations/LUM-553-tethered-egress-allowlist.md`

**Draft mirror:** `.cursor/adrs/LUM-553-tethered-egress-allowlist.md`

**Linear:** [LUM-553](https://linear.app/lumogis/issue/LUM-553/p2-tethered-in-process-egress-allowlist-defense-in-depth)

**Parent:** [LUM-194](https://linear.app/lumogis/issue/LUM-194/cloud-llm-privacy-mode-local-only-toggle-hard-enforcement-per-user-and) — Cloud LLM privacy mode (**ADR 147**)

**Related:** ADR 147 (routing policy — primary guarantee); ADR 148 (circuit breaker — egress wrapper sits inside breaker; `EgressBlockedError` excluded from failure accounting); LUM-507 (future plugin sandbox egress composition).

## Context

ADR 147 (LUM-194) enforces cloud-LLM privacy mode as a **deterministic routing-policy** guarantee at `orchestrator/config.py::get_llm_provider` and explicitly deferred **network-level** egress isolation as an optional later layer. LUM-553 revisits that deferral: ship an opt-in, default-off in-process egress allowlist as strictly-additive defense-in-depth behind the routing gate.

Constraints: native Core (Persona C) must have parity with Docker (ruling out OS/Docker-network isolation as the only story); routing policy must remain the sole hard guarantee; UI and operator docs must never claim "impossible egress" (the guard is bypassable via C extensions, `ctypes`, or raw syscalls).

During `/review-plan --arbitrate` R1, **process-wide ceiling** (`LUMOGIS_FF_EGRESS_GUARD_CEILING`, `activate(locked=True)`) was deferred from v1 — bootstrap ordering is infeasible before store health checks and locked hooks poison pytest isolation. v1 ships **scoped LLM wrap only**.

## Decision

Adopt an **opt-in, default-off in-process egress allowlist** using **`tethered==0.5.1`** (PEP 578 audit hooks):

1. **`LUMOGIS_FF_EGRESS_GUARD`** (registered in `features.py` as `EGRESS_GUARD`, default off) enables `EgressGuardingLLMProvider`, which enters `tethered.scope(allowlist)` around each `chat` / `chat_stream` call.
2. **Allowlist** is built dynamically per cache miss from env backends (Postgres, Qdrant, FalkorDB, ntfy, Ollama), loopback hosts, `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST`, cached dynamic Ollama hosts (60s TTL), and cloud API hosts only when effective privacy policy allows cloud (`effective_privacy_mode`).
3. **Wrapper chain:** `CircuitBreakingLLMProvider(EgressGuardingLLMProvider(adapter))` — egress scope is immediately before adapter network I/O; circuit breaker is outermost. `EgressBlockedError` does **not** increment breaker failures (misconfigured allowlists are operator errors, not upstream faults).
4. **HTTP contract:** non-stream chat → HTTP **503** `egress_blocked` (per-module shapes in `routes/chat.py` and `routes/api_v1/chat.py`); streaming → HTTP **200** with in-band SSE error via `loop._friendly_error` (headers already sent).
5. **`assert_remote_allowed`** is unchanged and remains the first gate in `get_llm_provider`.

**Process-wide ceiling** is deferred to a follow-up child issue under LUM-553.

**Option 2** (in-repo PEP 578 hook) remains the documented fallback if `tethered` supply-chain review fails; v1 proceeded with `tethered` (MIT, hash-pinned in `orchestrator/locks-bundled/`).

## Alternatives Considered

- **In-repo PEP 578 hook (Option 2):** no external dependency; fallback if `tethered` rejected at pin time.
- **Process-wide `activate(locked=True)` in v1:** rejected for bootstrap ordering + pytest isolation — deferred.
- **Docker `internal` network + proxy allowlist:** no native Core parity — ruled out (re-confirms ADR 147).
- **Keep deferred / status quo:** valid; tethered's 2026 wheels + tamper-resistant C guardian weakened the original deferral rationale.

Full comparison: `.cursor/explorations/LUM-553-tethered-egress-allowlist.md`.

## Consequences

- **Easier:** genuine second privacy layer for operators who opt in; identical on Docker and native Core; no new Docker service.
- **Harder / foreclosed:** commits to bypassable in-process model — honesty copy required; LUM-507 plugin egress must compose via `scope()` intersection, not a parallel mechanism; `tethered` dependency must be maintained (pinned + hash-pinned).
- **Future chunks must know:** routing policy (ADR 147) stays the only hard guarantee; legitimate local traffic (Ollama, Postgres, Qdrant, FalkorDB, ntfy) must not be blocked during scoped LLM calls; connector-heavy deployments may need `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST` when ceiling ships.

## Revisit conditions

- If `tethered` is abandoned or fails supply-chain review, switch to Option 2 or re-defer.
- If cloud call paths appear outside `get_llm_provider`, revisit both routing gate and egress scope.
- If LUM-507 formalises plugin sandbox egress, consolidate via `scope()` intersection.
- **Partially closed** by LUM-570 — see status history. Residual httpx keep-alive on unchanged allowlist documented; not a block bypass.

## Status history

- 2026-07-05: **LUM-570 follow-up (residuals closed on `agent/lum-570`)** — Production `EgressGuardingLLMProvider` unwraps OpenAI/Anthropic SDK-wrapped `tethered.EgressBlocked` via `__cause__`; `tethered.scope(..., allow_localhost=False)` so only explicit allowlist loopback hosts pass (not all `127.0.0.0/8`); `invalidate_llm_cache()` evicts `llm:` keys and closes httpx clients on eviction. PoC tests no longer use a harness shim.
- 2026-07-05: **LUM-570 PoC (partial closure)** — `orchestrator/tests/test_egress_guard_poc.py` proves real `OpenAILLM` socket I/O through sync `/v1/chat/completions` (non-stream blocked/allowed, upstream-error discrimination, streaming tool-loop second connect blocked, cache invalidation, warm-pool diagnostic). Blocked cases target **`93.184.216.34`** (non-loopback; tethered blocks at `connect` with no outbound packets). Happy path uses loopback fake server on **`127.0.0.1`**.
- 2026-07-05: Finalised by /verify-plan — v1 scoped-only implementation confirmed; ceiling deferred; ADR 153 (152 taken by LUM-473).
- 2026-07-05: Revised during /review-plan --arbitrate R1 — v1 scoped-only; ceiling deferred to follow-up child.
- 2026-07-05: Draft created by /explore --headless (LUM-553).
