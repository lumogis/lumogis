# ADR-241: Task-based model routing for background LLM jobs (LUM-877)
**Status:** Finalised
**Created:** 2026-11-06
**Last updated:** 2026-11-11 (amended for LUM-927)
**Decided by:** /explore LUM-230 (architecture) and /explore --headless LUM-877 (build-level rules), revised at /review-plan --arbitrate R1 and R2 (claude-opus-5-5)
**Finalised by:** /verify-plan --headless 2026-11-08 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-877-task-model-routing.plan.md`
**Explorations:** `.cursor/explorations/LUM-230-task-model-routing.md`, `.cursor/explorations/LUM-877-task-model-routing-build.md`
**Draft mirrors:** `.cursor/adrs/LUM-230-task-model-routing.md`, `.cursor/adrs/LUM-877-task-model-routing-build.md` (this record merges both)
**Linear:** LUM-877 (build), LUM-230 (evaluation parent)
**Builds on:** ADR 147 (`147-lum-194-cloud-llm-privacy-mode.md`, privacy mode gate), ADR 174 (`174-email-imap-signal-source.md`, email content stays on the box), ADR 151 (temporal validity and the judge pin), ADR 148 (LLM circuit breaker)
**Amends:** ADR 026 (`026-llm-provider-keys-per-user.md`): its background boot check now covers every task that runs without a user

## Context

Every model call in Core passes through `config.get_llm_provider()`, but each background job chose its model its own way. Four services hardcoded `"llama"` (entity extraction, relation extraction, session summary, the weekly review). Four environment variables chose the rest (`SIGNAL_LLM_MODEL`, `EMAIL_CLASSIFY_MODEL`, `LUMOGIS_TEMPORAL_EXTRACT_MODEL`, `LUMOGIS_TEMPORAL_JUDGE_MODEL`), and three email features shared one of them. A household could not tune cost and quality per job, or see in one place which model does what. Core always knows the task at the call site, so no prompt inspection is needed to pick a model.

Any design had to keep ADR 147's privacy gate as the final authority, keep ADR 026's guarantee that a job without a user can still resolve a model, keep email content on the box (ADR 174), and leave the default install unchanged. Reading the code showed three ways a simple build would quietly break that last promise:

- The temporal judge default is `llama3.1:8b`, an Ollama tag that `get_model_config` can never resolve.
- `memory.py` and `routines.py` sized their prompts for `llama` before resolving the model.
- `config.is_local_model` is a substring test, weaker than the email on-box check.

## Decision

1. **One resolver, a code registry of ten tasks.** `orchestrator/services/task_models.py` declares each background task as a `TaskSpec` with an explicit code default and fixed rules: `local_only`, `needs_tools`, `threads_user_id`, `destructive`, `email_boundary`. The tasks are `signal_enrichment`, `email_classify`, `email_reply_draft`, `email_compose_refine`, `entity_extraction`, `relation_extraction`, `session_summary`, `routine_weekly_review`, `temporal_extract` and `temporal_judge`. The operator lists models, in order, per task in a `tasks:` block of `config/models.yaml`. `resolve_task_model(task, *, user_id)` returns the first listed entry that exists, passes the task's rules, has a key for the user, is switched on and is allowed by privacy mode. Otherwise it returns the code default, unconditionally. It returns a model **name** only. It never builds an adapter. The result then goes through `privacy_mode.resolve_job_model` and `config.get_llm_provider`, unchanged. An empty table reproduces today's call exactly: the same model, the same budget, the same privacy path and audit rows.
2. **Per-task code defaults, no `background_default` in v1.** Every default is `llama` ~~except `temporal_judge`, which keeps `llama3.1:8b` (byte-identical, still unresolvable until LUM-568)~~.

   **Amended 2026-11-11 (LUM-927): `temporal_judge` defaults to `llama` too.** The maintainer chose `llama3.2:3b`, the model a standard install pulls, as the ADR 151 reference judge. `llama3.1:8b` was never pulled by default and, as an Ollama tag, never resolved, so the judge always ended `judge_failed`. The default and `TEMPORAL_JUDGE_REFERENCE_MODEL` are both `llama` and still change together. The ADR 151 live eval has not been run for any model, so the reference is a deployment pin, not a measured pass. `apply_or_propose` now refuses auto-apply unless the judge resolves to the reference tag (see the amendment to ADR 151 §4).
3. **"Local only" means on-box.** The `local_only` rule uses the on-box test (loopback, LAN, or a host in `EMAIL_LLM_ALLOWLIST`). The test moved from `email_classifier` into `task_models.is_on_box_local`, and `email_classifier` re-exports it, so there is one copy.
4. **Budgets follow the resolved model** at every call site.
5. **The retired variables are ignored, with a warning.** If one of the four variables is set, boot logs one `retired_model_env` WARNING with the variable name, never its value. CHANGELOG has a **Removed** entry.
6. **The boot check is generalised.** It warns about a malformed `tasks:` block (`task_config_invalid`), an unsatisfiable listed entry (`task_model_unsatisfiable`), a listed model without `context_budget` (`task_model_no_context_budget`), a keyed entry on a task that runs without a user (`task_model_needs_user`), and once, not per entry, when Ollama is unreachable at boot. A default boot makes no Ollama call. Under `AUTH_ENABLED=true` it refuses to boot only when a task that runs without a user has no keyless candidate. An unexpected error in the check logs `task_models_boot_check_failed`, and boot continues. The temporal judge warning stays in `plugins/graph/__init__.py`, because only the plugin knows the pipeline is live. A new `temporal_judge_not_reference` warning fires there when `LUMOGIS_TEMPORAL_AUTO_APPLY=true` and the judge is not the eval-reference class. *(Amended 2026-11-11, LUM-927: the warning compares the Ollama tag the judge resolves to, as the enforced pin does, and now says its verdicts are proposed, never applied.)*
7. **The temporal pipeline does not call `resolve_job_model`.** It keeps `get_llm_provider`'s hard privacy block (`assert_remote_allowed`). That block fails closed into `extract_failed` / `judge_failed` and writes the `privacy_mode_block` audit row. A privacy swap would be wrong for a destructive task: under the default `local_only` mode, the fail-closed `is_remote_model` would swap the unresolvable judge default to a 3B local fallback and start auto-applying unmeasured verdicts.
8. **Destructive tasks use their first listed entry only** (`TaskSpec.destructive`, today only `temporal_judge`). There is no skip filtering and no fall-through. If that model is unknown, blocked or keyless, `get_llm_provider` fails and the judgment is skipped. A transient Ollama failure therefore cannot move the judge to a later, unmeasured entry.
9. **`local_only` covers every task that can see email-derived text** (ADR 174): the three email tasks, `entity_extraction` (opt-in attachment text) and `routine_weekly_review` (imap signals in the review). For these tasks the resolver skips any listed entry that is not on-box. **Only the three email tasks (`email_boundary`) also refuse an off-box code default** (`resolve_on_box_model`, the LUM-170 boundary, unchanged). `entity_extraction` and `routine_weekly_review` keep their code default unconditionally, wherever the operator points `llama` (a Tailscale address included). The resolver opens no new path for email text to leave the box, and closes none that works today. The temporal tasks stay open: DOCUMENT episodes, email attachments included, have no hydration query and end `text_unavailable` before any LLM call. A note at `_HYDRATION_QUERIES` and a test tie the two: adding DOCUMENT hydration makes both temporal tasks `local_only` in the same change.
10. **`local_only` is task-wide for `entity_extraction`**, not keyed on input provenance. Its four callers share one call site, session text can quote email too, and the task was hardcoded to `llama`, so no capability is lost.
11. **The list is chosen from configuration when a job starts.** It is not runtime failover. ADR 148's breaker is separate, and a circuit-open skip stays in LUM-877 "Later".
12. **Admin diagnostics show every task.** `GET /api/v1/admin/diagnostics` gains an additive `tasks` array: each task's rules, its configured entries with a skip reason, the model it resolves to for the calling admin, and why the code default would fail downstream (`default_skip_reason`). Lumogis Web renders it as the **Background model tasks** table.
13. **ADR 026 is amended, not replaced.** Its boot check now covers every task that runs without a user.

## Implementation notes (as built)

- **Boot refusal rule, narrowed in implementation.** The plan refused boot when no candidate was "known with no `api_key_env`". As built, it refuses only when at least one candidate is known and keyed, and none is known and keyless. A candidate the lookup cannot find (for example, while Ollama is down) never causes a refusal. This keeps ADR 026's "unknown model → warn, not raise" rule (`test_unknown_default_under_auth_warns_does_not_refuse`).
- **A malformed first entry on a destructive task is not replaced by the second.** `configured_list` returns `()` (the code default) when a destructive task's first raw item is not a model name, so a bad first item cannot move the judge to the next entry (`test_destructive_task_malformed_first_item_is_not_promoted`).
- **Known gap at `signal_processor._call_llm`:** the existing guard refuses any keyed model when there is no user, even under `AUTH_ENABLED=false`, where the resolver accepts a keyed first entry with a global key. A keyed `signal_enrichment` entry on a run with no user then does nothing, instead of falling to the next entry. The guard predates LUM-877, and the in-code note tells operators to list a local model first.

## Alternatives considered

- **Plain task-to-model map with no rules:** rejected. A cloud model could sit in an email slot until calls were silently skipped.
- **Content-based routing (RouteLLM, semantic routers):** rejected for background tasks, which already know their task. Deferred for chat "Auto" to LUM-121 / LUM-563.
- **Routing in LiteLLM:** rejected as the routing layer. It is optional, off the default path, and knows neither the task, the user nor privacy policy. It remains a valid model target.
- ~~**Judge defaults to `llama`:** rejected. It would turn on auto-applied verdicts from a 3B model the eval never measured.~~ *Amended 2026-11-11 (LUM-927): adopted by the maintainer as the reference judge. Auto-apply stays default-off, and the eval still has to clear the bar on this reference before it is recommended.*
- ~~**A new `llama-judge` alias:** deferred to LUM-568, together with enforcing the ADR 151 pin in `apply_or_propose`.~~ *Amended 2026-11-11 (LUM-927): not needed. The existing `llama` entry is the reference, and the pin is enforced in `apply_or_propose`.*
- **`background_default` with a per-task opt-out:** rejected for v1. No acceptance line asks for it.
- **`config.is_local_model` as the local rule:** rejected. A remote Ollama would pass for email.
- **Refusing an off-box code default on every `local_only` task:** rejected (arbitration R2). It would stop entity extraction and the weekly review on any install whose `llama` is not on a default host, loopback or private address, even with an empty `tasks:` table.
- **Gating `entity_extraction` on input provenance:** rejected for v1 (§10).
- **Silent retirement, or refusing to boot while a retired variable is set:** rejected. The first loses an operator's tuning with no message. The second is too harsh after a routine update.
- **Per-user task preferences and a settings UI in v1:** deferred. The operator's file comes first.

## Consequences

- **Easier:**
  - One place to see and tune which model does what.
  - Tasks that were hardcoded are now configurable.
  - An empty table is provably byte-identical, and tests pin it.
  - Diagnostics explain every skipped entry in the vocabulary of `GET /api/v1/models`, plus `unknown`, `not_on_box`, `no_tools` and `error`.
- **Harder:**
  - An operator who wants one model for every task lists it per task.
  - Reading a call site goes through one more indirection.
- **Future chunks must know:**
  - Every new background LLM feature declares a `TaskSpec` with an explicit default. It never reads its own env var or hardcodes an alias at the call site (LUM-516, LUM-110, LUM-231 and LUM-122 are affected).
  - Destructive tasks never inherit a shared default and never call `resolve_job_model`.
  - A task that gains an email-derived input becomes `local_only` in the same change.
  - `config/models.yaml` must not loosen a code rule. The table can only choose among models the rules allow.
  - If `config.model_disabled_reason` gains a reason, add it to `SkipReason`, `AdminDiagnosticsTaskSkipReason` and the web labels in the same change.
  - ~~The judge default stays unresolvable until LUM-568 adds a resolvable alias and enforces the pin.~~ *(Done by LUM-927: the default is `llama` and the pin is enforced.)* Change `TASKS["temporal_judge"].default` and `TEMPORAL_JUDGE_REFERENCE_MODEL` together, and keep `TEMPORAL_JUDGE_REFERENCE_TAG` equal to the tag that entry names.

## Revisit conditions

- **A shared default:** if households ask for one setting for every background task, add `background_default`, with destructive tasks opted out.
- ~~**The judge alias:** when LUM-568 lands, switch the `temporal_judge` default to the resolvable alias.~~ *Met 2026-11-11 by LUM-927.*
- **The reference judge:** if the ADR 151 eval runs and `llama3.2:3b` cannot meet the bar, pick a new reference and change the default, `TEMPORAL_JUDGE_REFERENCE_MODEL` and `TEMPORAL_JUDGE_REFERENCE_TAG` together.
- **The local test:** if `config.is_local_model` gains the on-box host test, fold `task_models.is_on_box_local` back into it.
- **Cloud entity extraction:** if operators ask to route ordinary documents to a cloud model, add a provenance gate instead of loosening `local_only`.
- **Temporal DOCUMENT hydration:** if `_HYDRATION_QUERIES` gains DOCUMENT, make both temporal tasks `local_only` in the same change.
- **Chat "Auto" / modes:** if LUM-121 adopts query, action and plan modes with their own model policy, revisit whether they map onto tasks. Chat "Auto" is expected to reuse `TaskSpec.needs_tools` and `explain_task`.
- **Per-member preferences:** if households ask for them, or LUM-357 context profiles ship, revisit per-user overrides.
- **Hot-path cost:** if profiling shows the per-resolution model lookup on the signal path, add a short TTL at `_ModelLookup`.
- **The boot warning:** one public release after LUM-877 ships, drop the `retired_model_env` warning and `RETIRED_MODEL_ENV`.

## Status history

- 2026-11-06: Draft created by /explore LUM-230 (`.cursor/adrs/LUM-230-task-model-routing.md`)
- 2026-11-08: Build-level supplement drafted by /explore --headless LUM-877 (`.cursor/adrs/LUM-877-task-model-routing-build.md`)
- 2026-11-08: Revised at /review-plan --arbitrate R1: no `resolve_job_model` in the temporal pipeline; the judge warning stays in the plugin, plus the AUTO_APPLY guard; destructive tasks use their first entry only; `local_only` extended to `entity_extraction` and `routine_weekly_review`
- 2026-11-08: Revised at /review-plan --arbitrate R2: only the three email tasks (`email_boundary`) refuse an off-box code default; `local_only` is task-wide on `entity_extraction`; ADR 026 is amended
- 2026-11-08: Finalised by /verify-plan --headless. The implementation confirmed the decision. Two narrowings and one known gap were recorded under Implementation notes. This record merges the LUM-230 draft and the LUM-877 supplement.
- 2026-11-11: Amended (LUM-927). The maintainer chose `llama3.2:3b` (the `llama` entry, which a standard install pulls) as the default and reference temporal judge. `llama3.1:8b` never resolved and was never pulled, so the judge always ended `judge_failed`. The ADR 151 live eval has still not run for any model, so this is a deployment pin. `apply_or_propose` now refuses auto-apply unless the judge is the reference (Decision §2 and §6, Alternatives, Future chunks, Revisit conditions).
