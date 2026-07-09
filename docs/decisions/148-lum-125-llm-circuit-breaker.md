# ADR-148: LLM circuit breaker + retry-loop audit (LUM-125)

**Status:** Finalised

**Created:** 2026-06-30

**Last updated:** 2026-06-30

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-30 (Composer)

**Plan:** none — cherry-picked from `origin/lumogis/lum-125-audit-and-add-circuit-breakers-to-all-retry-loops-prevent` (`6de6bc863`)

**Exploration:** `.cursor/explorations/lum_125_llm_circuit_breaker_retro.md`

**Draft mirror:** `.cursor/adrs/lum_125_llm_circuit_breaker.md`

**Linear:** [LUM-125](https://linear.app/lumogis/issue/LUM-125/audit-and-add-circuit-breakers-to-all-retry-loops-prevent-runaway-api)

**Related:** ADR-039 (injection sanitisation — LUM-125 follow-up from LUM-127); `config.get_tool_chain_cap`; LUM-122 (autocompact) and LUM-109 (consolidation) as future `get_breaker` consumers.

## Context

LUM-125 asks us to audit every retry loop in the orchestrator and ingest
pipeline for runaway-spend risk — a missing failure cap on a retry loop can
burn API budget indefinitely (Claude Code telemetry: ~250k wasted calls/day
from one uncapped path). The deliverable is an audit plus caps + logging for
anything missing.

The audit found that **almost every retry loop is already bounded** by an
attempt cap. The genuine gap is not an uncapped per-call loop — it is the
absence of a **cross-call consecutive-failure breaker** on the expensive LLM
path: when a model is misconfigured or its upstream is down, every new chat
request still pays to hit it and fail.

Shipped via agent branch cherry-pick onto `dev` (`740bfc460`); ADR numbered
**148** (146 taken by LUM-126 feature flags).

### Retry-loop audit

| Site | Mechanism | Capped? | Logged? | Action |
| --- | --- | --- | --- | --- |
| LLM provider calls (`loop` → `provider.chat` / `chat_stream`) | none (cross-call) | ✗ → **breaker added** | ✓ (`circuit_opened`) | **This ADR** |
| Agent tool-call loop (`loop._run_session_loop`) | `MAX_TOOL_ROUNDS` + `get_tool_chain_cap` (default 10) | ✓ | ✓ (`tool_chain_cap`) | none |
| Batch queue (`services/batch_queue`) | `BATCH_QUEUE_MAX_ATTEMPTS` | ✓ | ✓ | none |
| Proposal queue (`services/proposal_queue`) | `ACTION_PROPOSALS_MAX_ATTEMPTS=3` + exp. backoff → `dead` | ✓ | ✓ | none |
| Memory purge (`services/memory_purge`) | `_RETRY_ATTEMPTS=3` + backoff; sweeper `_SWEEPER_MAX_ATTEMPTS=20` | ✓ | ✓ | none |
| MCP token mint (`services/mcp_tokens`) | `_MINT_COLLISION_BUDGET` | ✓ | ✓ | none |
| Ingest progress (`services/ingest_progress`) | `max_attempts` param | ✓ | ✓ | none |
| Paperless source (`adapters/paperless_source`) | `_MAX_ATTEMPTS` | ✓ | ✓ | none |
| Qdrant scroll (`adapters/qdrant_store` `while True`) | pagination — terminates when `offset is None` | ✓ (bounded by data) | n/a | none |
| Ollama client (`ollama_client`) | one-shot `httpx` calls, no retry loop | n/a | n/a | none |
| Embedding-readiness job (`main` scheduler) | self-removing poll, exits on embedder active | ✓ (poll, not spend) | ✓ | none |
| Anthropic SDK internal retries | SDK `max_retries` default | ✓ | SDK | now also under the breaker |
| Consolidation (LUM-109), autocompact (LUM-122) | not yet built | — | — | future `get_breaker` consumers |

## Decision

Add a small, reusable **consecutive-failure circuit breaker** primitive
(`services/circuit_breaker.py`) and wire it into the LLM provider boundary.

- `CircuitBreaker` — thread-safe, per-operation: opens after `max_failures`
  consecutive failures, fails fast with `CircuitOpenError` (a `RuntimeError`,
  so existing broad handlers map it to HTTP 503) for a cooldown window, then
  allows one half-open probe that closes on success or re-opens on failure. A
  registry (`get_breaker`) shares state across call sites by name.
- Wiring: `config.get_llm_provider` wraps the constructed adapter in
  `CircuitBreakingLLMProvider`, keyed by the existing per-user+model cache key,
  so one user's failing model does not trip another's. Transparent to
  `loop` — same `chat` / `chat_stream` contract. Privacy-mode gate
  (`assert_remote_allowed`) runs **before** adapter construction (unchanged).
- Defaults: `cloud_llm` ceiling = 3 (matches Claude Code's
  `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES`), 30 s cooldown. Operator overrides
  `LUMOGIS_LLM_CIRCUIT_MAX_FAILURES` / `LUMOGIS_LLM_CIRCUIT_COOLDOWN_S`;
  **kill-switch** `LUMOGIS_LLM_CIRCUIT_ENABLED=false` returns the bare adapter.
- A circuit open is logged as a structured operational event
  (`event=circuit_opened`), **not** written to the per-user `audit_log` table
  (that table is for user actions, not infra health).

The other audited loops are already capped, so no further code change is made
to them; they are recorded above for traceability.

## Alternatives considered

- **Per-call retry caps only:** rejected — audit showed per-call loops are
  already bounded; the spend leak is cross-call on the LLM hot path.
- **Fail-open (log only):** rejected — LUM-125 intent is to stop paying for a
  dead upstream after N failures; kill-switch env var provides operator escape.
- **Session-scoped breaker (Claude Code model):** deferred — Lumogis keys by
  user+model cache slot with cooldown; session-long open is achievable via
  cooldown tuning but not identical to Claude's session disable.

## Consequences

- After `LUMOGIS_LLM_CIRCUIT_MAX_FAILURES` consecutive failures for a
  user+model, chat returns 503 until the cooldown elapses — a deliberate,
  reversible behaviour change on the chat hot path (kill-switch available).
- Future expensive subsystems (consolidation, autocompact) reuse
  `get_breaker(...)` rather than re-implementing failure counting.
- `snapshot()` exposes breaker state for a future admin/diagnostics surface
  (not wired to an endpoint in this change).
- Breaker registry state is **not** cleared when `invalidate_llm_cache_for_user`
  evicts cached providers — a credential fix may still wait for cooldown unless
  the operator disables the breaker or time elapses.

## Revisit conditions

- Wire `snapshot()` to an admin diagnostics endpoint when operator UX needs
  visible breaker state.
- Add user-facing `circuit_open` refusal copy (LUM-127 refusal-surface
  programme) instead of generic chat errors.
- When LUM-122 / LUM-109 land, attach `get_breaker` at those expensive paths.
- Reset breaker on credential rotation if product wants immediate retry after
  key fix (today: cooldown-only recovery).

## Linear linkage (Product OS)

- **Existing issue:** [LUM-125](https://linear.app/lumogis/issue/LUM-125) — move to **Done** via `/linear-update apply-closure LUM-125 --done` after operator review.
- **New issue needed:** no — scope covered by LUM-125.
- **Historical evidence only:** no — active tech-debt issue closed by this shipment.
- **Follow-ups created/mapped:** LUM-122 / LUM-109 (future breaker consumers); LUM-127 refusal UX (`circuit_open` category) — existing Linear items, not new portfolio rows.

## Testing retrospective

| | |
| --- | --- |
| **Added** | `orchestrator/tests/test_circuit_breaker.py` — **15** unit tests (primitive states, registry, `wrap_llm_provider`, stream paths including early `end` break, kill-switch) |
| **Run** | `.venv/bin/python -m pytest orchestrator/tests/test_circuit_breaker.py -q` → **15 passed** (after `ac920ee30` stream fix) |
| **Regression note** | `test_llm_provider_per_user.py` cloud-model cases fail locally when LUM-194 privacy mode defaults block remote models — pre-existing on `dev`, not introduced by LUM-125 |
| **Skipped** | none for circuit-breaker suite |
| **Gaps** | No integration test that chat route returns 503 after N real upstream failures; no test for breaker persistence across cache invalidation |
| **Follow-up test tasks** | Optional: route-level 503 assertion after mocked consecutive failures; privacy-mode test fixture alignment for `test_llm_provider_per_user` |
| **automated-test-strategy.md** | no update required (unit coverage sufficient for v1) |
| **Release skills** | no change |

## Status history

- 2026-06-30: Shipped on agent branch `6de6bc863` (Claude Opus 4.8).
- 2026-06-30: Cherry-picked to `dev` (`740bfc460`); ADR renumbered **148**.
- 2026-06-30: Finalised by `/record-retro` (retrospective).
- 2026-07-02: Follow-up fix on `dev` (`ac920ee30`) — `CircuitBreakingLLMProvider.chat_stream` records success in `finally` when `loop.py` breaks on `end` without exhausting the generator (GeneratorExit path); merged from `cursor/critical-bug-investigation-1f68`.
