# ADR-099: Continue Site pattern for orchestrator session state (LUM-128)

**Status:** Finalised
**Created:** 2026-06-06
**Last updated:** 2026-06-15
**Decided by:** `/explore LUM-128`; revised per Thomas review 2026-06-06; implementation verified `/verify-plan LUM-128`
**Linear issue:** LUM-128 (parent LUM-122)
**Exploration:** `.cursor/explorations/LUM-128-continue-site-pattern.md`
**Plan:** `.cursor/plans/archived/LUM-128-continue-site-pattern.plan.md`

## Context

LUM-128 is **foundation-laying** for parent **LUM-122** (compaction), not a live bug fix. The orchestrator chat path (`orchestrator/loop.py`, `orchestrator/routes/chat.py`) previously mutated turn state imperatively (`messages.append(...)`, in-place `ToolChainBudget` counters). That ordering is **latent risk today**: there is no mid-turn persistence — a failed request discards the in-memory list and nothing inconsistent reaches Postgres. The risk becomes **active** when LUM-122 (compaction reading loop state) or LUM-162-class mid-turn persistence lands.

**Schedule gate:** Implement LUM-128 only while **LUM-122 remains committed** on the roadmap. If compaction slips, defer this refactor.

Compaction (LUM-122) and precompact (LUM-156) need stable continue sites; implementing them on a split between `_inject_context` and `_stream_loop` would fork state management.

## Decision

Adopt a **phased sync-generator Continue Site** in the AGPL orchestrator (**slices A–B only** in LUM-128):

1. Add `SessionParams` (immutable) and `SessionState` (`@dataclass(frozen=True)`) in `orchestrator/models/session_state.py` with `dataclasses.replace()` at every transition. **v1 fields:** `messages`, `turn_count`, `tool_chain_budget`, `transition`, `terminal` — **no** `compaction_state`, `denial_state`, or `error_count`.
2. Refactor `ask()` and `ask_stream()` to share `_run_session_loop()` through **four Lumogis continue sites** in **one PR**: pre-request (caller-seeded), model call, tool dispatch, turn advance/terminate.
3. Enforce this **transcript invariant** (test directly in `test_session_loop_transitions.py`):

   > Within a single `ask()` / `ask_stream()` invocation, `SessionState.messages` is never observable as *assistant appended, tool results missing*. A tool round goes from *N* to *N+1+K* in one `replace()`.

   Shallow `tuple` wrap is sufficient (reassignment-atomicity); deep-copy only if tests prove provider in-place dict mutation.
4. Mark SITE_PRE_REQUEST with a **comment-only** seam for future LUM-122 compaction — **no** `compaction_state` stub until LUM-122 `/explore` locks snapshot shape.
5. **Do not** convert to `async def` session generators in v1 — sync LLM providers; FastAPI `StreamingResponse` threadpool unchanged. Non-streaming `ask()` discards cheap `StreamEvent` instances via `_finish_session_loop()`.

**Slice B:** `on_loop_event(event, state)` callback at each continue site; `_run_session_loop` returns `(SessionTerminal, SessionState)`.

**Intentional transcript delta:** MAX-tool-round forced-final assistant row is **appended** to `SessionState.messages` (legacy loops omitted it from the in-memory list). HTTP return text from `ask()` is unchanged.

**Out of LUM-128:** `denial_state` / LUM-131 coupling (independent Ask/Do layer); compaction field stubs (LUM-122 exploration first).

## Alternatives Considered

- **Full async `session_loop` mirroring Claude Code's seven sites** — rejected for v1: sync providers, no cancellation win yet.
- **Status quo with defensive rollback** — rejected: no named sites for LUM-122.
- **LangGraph / external FSM** — rejected: new dependency, disproportionate for `MAX_TOOL_ROUNDS=2`.
- **Slices C/D (compaction/denial stubs in LUM-128)** — rejected: negative work before LUM-122 shape is known.

Full analysis: `.cursor/explorations/archived/LUM-128-continue-site-pattern.md`

## Consequences

**Easier:**

- LUM-122 can add `compaction_state` after its exploration ADR without re-plumbing two code paths.
- Transcript invariant enforced before any in-flight state consumer ships.
- LUM-139 metrics can subscribe via slice-B `on_loop_event(event, state)` callback (in-process; not HTTP/SSE).

**Harder:**

- `loop.py` refactor touches all chat paths — regression tests required.
- Dict contents inside `tuple` remain mutable — documented limitation; not part of v1 invariant.

**Future chunks must know:**

- `_inject_context` in `chat.py` remains site 0 until compaction moves inline.
- Async generator conversion is a separate revisit.

## Revisit conditions

- **LUM-122 descoped or deferred** → defer or cancel follow-on compaction integration.
- LUM-122 exploration locks compaction snapshot → extend `SessionState` in a **LUM-122 child plan**, not retroactively in LUM-128 closure.
- LLM providers async-native + mid-stream cancellation required → revisit async `session_loop`.
- Test proves provider mutates message dicts in place → add deep-copy policy.

## Status history

- 2026-06-06: Draft created by /explore LUM-128
- 2026-06-06: Revised — foundation framing, transcript invariant, slices A–B only, drop v1 stubs (Thomas review)
- 2026-06-15: Revised during /review-plan --arbitrate R1 — Slice B integration is `on_loop_event(event, state)` callback; generator returns `(SessionTerminal, SessionState)`.
- 2026-06-15: Finalised by /verify-plan — implementation confirmed decision (slices A–B shipped).
