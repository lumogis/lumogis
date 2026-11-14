# ADR-189: Email compose refine — on-box tone/length transforms
**Status:** Finalised
**Created:** 2026-07-24
**Last updated:** 2026-07-24
**Decided by:** /explore (claude-opus-4-8); finalised by /verify-plan (LUM-684)

## Context
The email composer (LUM-652) suggests an on-box reply draft; dictation/typed text stays verbatim (STT does zero post-processing). The user wanted an **editing-assistance** layer: on-demand transforms of the *current composer body* — preset tone/length changes (Formal · Shorter · Longer · Clearer · Friendlier · Fix grammar) plus a free-form instruction. Because refine **rewrites the user's own text**, it must be **non-destructive** (preview → Apply/Undo). The mechanism is the same local-first, privacy-guarded LLM path LUM-652 established; the decisions were where the code lives, who owns the preset prompts, the UX, and the core/app boundary.

## Decision
Ship refine in **core** as an **on-demand, stateless** text transform reusing the on-box LLM path. A new `services/local_llm_task.py::run_bounded_completion` was **extracted** from `email_reply_draft.py` so **draft and refine share ONE concurrency cap** (`_LLM_TASK_SLOTS(3)`) against the shared on-box Ollama. A new `services/text_refine.py` owns the **preset→instruction map** server-side (client sends a preset key or a free-form instruction; the user's text is delimited from the instruction in a `<body>` block, free-form length capped, system prompt constrains "output only the rewritten body"). `POST /api/v1/email/refine` (`require_user`, separate LLM-call rate bucket) returns `{text, available}`; the model is resolved **once** per request. In `EmailComposer`, a refine row (preset chips + custom instruction) shows a **non-destructive preview with Apply / Undo / Discard**, fed by an injected `refine` fn (same DI pattern as LUM-653's `transcribeVoice`), Review gated while refining, stale responses dropped (a live-`body` ref guards against clobbering concurrent edits), and disabled with a note when no local model is configured. No new dependency, Docker service, env var, or migration.

**Aligned token budgets (deliberate):** because refine replaces the user's own words, the request `text` bound (≤ 8000 chars), the input-token budget (`_REFINE_INPUT_TOKENS = 2000`), and the output ceiling (`_REFINE_OUTPUT_TOKENS = 3000`) are aligned so a valid body is fully rewritten and never silently truncated — unlike the draft, which truncates a *received* email as mere context.

## Alternatives Considered
- **Duplicate the deadline/concurrency helper** — two independent caps = uncoordinated load on the one shared model. Rejected for the extracted shared cap.
- **Client-owned preset prompts** — scatters wording into the bundle, spoofable, harder to tune. Rejected; presets live on the server.
- **Inline replace / diff view** — inline briefly destroys the draft before the user sees the rewrite; diff over-builds the UI for short bodies. Rejected for non-destructive preview.
- **App-scope: proactive auto-toning / learning-from-edits / cloud tone-models** — proprietary/proactive territory, deferred (LUM-654-adjacent).
- **Persisting draft text / streaming / new dependency** — unnecessary; refine is stateless text-in→text-out on the shipped on-box path.
- Full detail: `cursor/explorations/LUM-684-email-compose-refine.md`.

## Consequences
**Easier:**
- Reuses the privacy-guarded, concurrency-capped on-box LLM path and the composer DI seam — small additive surface.
- One shared concurrency cap protects the single on-box model the whole household shares (draft + refine).
- Non-destructive preview preserves trust when rewriting the user's own words.

**Harder / foreclosed:**
- Commits to **on-demand, stateless, plain-text, preview-then-apply** refine in **core**; proactive/auto-tone and learning-from-edits stay app-scope.
- Rewrite quality depends on the household's configured local model (small models may under-deliver — a documented revisit condition).

## Revisit conditions
- **Local-model rewrite quality/latency is poor** → revisit model routing / a larger default for refine.
- **Users want granular control** → revisit selection-scoped refine and/or a diff accept-reject UI.
- **An autonomous/agentic sender (LUM-654) lands** → confirm it reuses `refine_text` / `local_llm_task` rather than re-implementing, and that proactive/learning stays app-scope.

## Status history
- 2026-07-24: Draft created by /explore (LUM-684).
- 2026-07-24: Finalised by /verify-plan (LUM-684) — implementation confirmed the decision (shared `local_llm_task` cap, `text_refine` service, `POST /api/v1/email/refine`, non-destructive preview refine row). Review hardened it: Sonnet R1 caught that the shared-cap refactor would crash an existing draft test that reaches into the deleted internals (fixed: keep the deadline knob, retarget the concurrency test); Fable R2 caught that refine *inverts* draft's risk and the budgets were mismatched (fixed: aligned the three budgets, stale-response guard, single model resolution). Finalised copy at docs/decisions/189-email-compose-refine.md.
