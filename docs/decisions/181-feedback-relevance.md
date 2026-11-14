# ADR-181: Feedback → relevance — per-viewer weighted-term learning

**Status:** Finalised
**Created:** 2026-07-22
**Last updated:** 2026-07-22
**Decided by:** Sonnet 5 (/explore LUM-649); revised by Sonnet 5 (/review-plan --arbitrate R1, LUM-649); finalised by Sonnet 5 (/verify-plan LUM-649)


## Context

LUM-649 (chunk #7 of the LUM-641 email arc) needs 👍/👎/"Not important" feedback on signal items to measurably, legibly change future flagging — replacing the currently invisible, env-only `SIGNAL_RELEVANCE_THRESHOLD` and the disconnected "Not important" triage button that today has zero learning effect. The existing relevance engine (`services/signal_processor.py::match_relevance`, per-user `RelevanceProfile`) and feedback pipe (`POST /feedback` → `feedback_log` + `Event.FEEDBACK_RECEIVED`) both already exist but are unconnected for signals — the only registered `FEEDBACK_RECEIVED` listener is the proprietary graph plugin's `on_feedback_received`, scoped to `item_type="entity"` only. A critical constraint surfaced during exploration: `relevance_score` and `importance_score` are computed once at ingest and copied verbatim onto shared household projections (`services/projection.py`), so feedback cannot safely mutate those stored columns without one member's feedback silently changing what another member sees — the same hazard LUM-643 solved for triage state via a per-user table and a LATERAL read-time overlay, never a column on `signals`.

## Decision

Extend the existing weighted-sum relevance model with **learned term strengths**, applied as a **per-viewer overlay at read time** — never by mutating the shared `relevance_score`/`importance_score` columns. A new core service, `services/relevance_learning.py`, is invoked by a **direct, synchronous function call from `routes/signals.py::record_feedback`** — not a hook listener — and on explicit 👍/👎 applies a small bounded delta (±0.15, clamped to [-1, 1], no decay in v1) to matching topic/category/entity terms (sender folded into the entity dimension) in new per-user `learned_*_weights` JSONB columns on `relevance_profiles`. `match_relevance` gains a new, separate **additive sixth component** (clamped to ±0.3) after the existing unchanged 5-term weighted sum — not a change to how the five existing terms are scored — fully backward compatible with a zero-feedback profile (the new component evaluates to exactly `0.0`). The needs-you/good-to-know lane decision consumes the caller's learned delta **additively**: a new per-caller `learned_relevance_delta` field on `GET /signals`, computed **in Python from a single per-request profile-weights point-read** (not a LATERAL join — `relevance_profiles` is one row per user, `UNIQUE(user_id)`, not per-(user,signal) like `signal_triage`), is added to `importance_score` before the lane threshold check (clamped to ±0.6, deliberately equal to the lane threshold itself) — the AI's baseline judgment remains the floor/ceiling; feedback shifts the margin, it does not override it. Learning is explicit-feedback-only in v1 (implicit events like opens/dismissals are recorded but not learned from — too noisy and too invisible for a chunk whose purpose is legibility).

**Transparency is a first-class requirement, not just a toast.** `POST /feedback`'s response gains an `effect` field naming which term(s) moved (in-the-moment confirmation: "I'll flag fewer like this: {term}"), **and** a new durable, browsable view ships in the same chunk: `GET /profile/learned` (human-readable current learned weights) plus a web settings section listing every demoted/promoted term the profile has learned. A message that vanishes after a few seconds is still effectively invisible a day later — trust requires the effect to remain inspectable, not just glimpsed once.

**Revision note (2026-07-22, `/review-plan --arbitrate R1`):** the three mechanisms above — direct synchronous call, additive sixth component, Python-side per-request computation — replace this ADR's original sketch of a second `FEEDBACK_RECEIVED` hook listener, an `effective_term_weight()` helper folded into the five existing components, and a LATERAL join. Each substitution was reasoned through during `/create-plan` and confirmed correct during `/review-plan --self` and `--critique`: `hooks.fire_background` is asynchronous, so a hook listener cannot reliably report which term moved within the same HTTP response the transparency requirement demands; a separate additive component avoids restructuring the five terms whose exact shape this chunk must preserve byte-for-byte; and `relevance_profiles` is genuinely the wrong shape for a LATERAL. The outcome (per-viewer overlay, additive, non-mutating, feedback shifts margin not floor/ceiling) is unchanged — only the three mechanisms are corrected to match what the plan actually builds.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-649-feedback_relevance.md`.

- **River (online-ml library)** — genuine, well-maintained, self-hosted-friendly online-ML library; ruled out for v1 as a new dependency solving a problem the existing weighted-sum system already models, with a model output that is harder to explain in plain language than a named term weight.
- **Multi-armed bandits (MABWiser/Mab2Rec)** — built for "which of N competing items to show"; framing mismatch against Lumogis's binary/continuous relevance-threshold problem.
- **Embedding-centroid nudge (reuse Qdrant/Ollama embeddings already computed at ingest)** — cheapest to build technically, but fails the legibility requirement hardest ("close to things you disliked" isn't a nameable reason); deferred as a possible v2 layered on top of, not replacing, the term-weight system.

## Consequences

**Easier:** the "Not important" button and a new 👍 affordance become genuinely learning actions instead of dead ends; the relevance engine gains a real feedback loop without a new dependency, new Docker service, or new Protocol; the pattern (per-viewer overlay for feedback-affected state) generalises to any future signal type.

**Harder / committed:** `services/relevance_learning.py` is invoked directly and synchronously from `record_feedback`, not via the hook system — `Event.FEEDBACK_RECEIVED` continues to reach only the proprietary graph plugin's `item_type="entity"` listener, so this chunk does **not** prove multi-listener hook fan-out (a revisit condition if a future chunk needs that pattern); `relevance_score`/`importance_score` as stored columns are no longer the sole source of a viewer's needs-you-ness once feedback exists for that user — any future code reading them directly for lane-adjacent decisions must be aware a per-caller overlay may apply; a new migration adds JSONB weight columns to `relevance_profiles` (additive, backward compatible).

**Future chunks must know:** the sender/`from_addr` matching axis is new (folded into the existing entity weighted component, not a new one) — the first time Lumogis tracks "who" as a first-class relevance dimension for email. A settings-page UI for *editing* the profile's explicit tracked_* lists (the still headless `PUT/GET /signals/profile` API) remains out of this chunk's scope — only a *read-only view of learned weights* ships here, not a manual-edit UI for the explicit lists.

## Revisit conditions

- If household feedback volume shows term-weighting alone under- or over-corrects (too coarse, too noisy), revisit layering the embedding-centroid nudge (Option 4) on top as a tie-breaker or diversity signal — not a replacement.
- If implicit feedback (item_opened, signal_dismissed) proves too valuable to leave unlearned-from, revisit the explicit-only v1 scoping.
- If a second signal type (beyond email) needs feedback-driven relevance, revisit whether the sender-as-entity folding still makes sense for a type with no analogous "who" field.

## Status history
- 2026-07-22: Draft created by /explore (LUM-649).
- 2026-07-22: Revised during `/review-plan --arbitrate R1` (Sonnet 5, on a Fable 5 critique) — corrected three mechanism descriptions to match what the plan actually builds: direct synchronous invocation (not a second `FEEDBACK_RECEIVED` hook listener), an additive sixth `match_relevance` component (not `effective_term_weight()` inside the five existing components), and Python-side per-request computation (not a LATERAL join). Also fixed the `GET /profile/learned` route path (was mis-typed `/signals/profile/learned`). Outcome and rationale unchanged; only the recorded mechanisms were wrong. Status → Draft (revised).
- 2026-07-22: Finalised by `/verify-plan` (LUM-649) — implementation confirmed the revised decision exactly: `services/relevance_learning.py` invoked synchronously from `routes/signals.py::record_feedback`; additive ±0.3 `learned_component` in `match_relevance` (proven zero-impact for empty profiles by `test_zero_learned_weights_matches_pre_lum649_score`); per-caller ±0.6 `learned_relevance_delta` computed in Python on `GET /signals`; migration 058 additive JSONB columns; visibility-scoped `_load_signal_row` via `visible_filter`. Status → Finalised; canonical copy at `docs/decisions/181-feedback-relevance.md`.
