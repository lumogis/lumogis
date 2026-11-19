# ADR-250: Chat session summaries stay on the box (LUM-899)
**Status:** Finalised
**Created:** 2026-11-10
**Last updated:** 2026-11-16 (Decision addendum for LUM-983)
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-10 (claude-opus-5-5)
**Plan:** none. This shipped before a formal plan / verify cycle for the slice (`exec:claude` session).
**Exploration:** `.cursor/explorations/session_summary_local_only_retro.md`
**Draft mirror:** `.cursor/adrs/session_summary_local_only.md`
**Linear:** LUM-899; LUM-983 (Decision addendum, 2026-11-16)
**Amends:** ADR 241 (`241-lum-877-task-model-routing.md`) §9 and §10. `session_summary` joins the `local_only` set.
**Builds on:** ADR 174 (`174-email-imap-signal-source.md`, email content stays on the box), ADR 147 (privacy mode)

## Context

LUM-877 (ADR 241) moved every background LLM job onto a task table. ADR 241 §9 made `local_only` every task that can see email-derived text. §10 gave one of the reasons `entity_extraction` is `local_only`: "session text can quote email too". The `session_summary` task was left open.

Both tasks read the same session text in `orchestrator/services/batch_handlers/session_end.py`. `summarize_session(msg_dicts)` gets the messages and `extract_entities(session_text)` gets them joined. Suppose privacy mode allows cloud and an operator lists a remote model under `tasks.session_summary`. When a chat that quoted an email ends, the whole conversation goes to that remote model, while entity extraction of the same text stays on the box. Before LUM-877 the summary was hardcoded to `llama`, so this path did not exist. The weekly code review found it (LUM-899).

## Decision

`session_summary` is `local_only=True` in `orchestrator/services/task_models.py`. The resolver skips any listed entry that is not on-box (loopback, LAN, or a host in `EMAIL_LLM_ALLOWLIST`) with reason `not_on_box`, and returns the code default `llama`. This holds under both privacy modes. As for `entity_extraction`, the rule is task-wide and not keyed on whether a given session quoted email. `email_boundary` stays `False`, so the code default is kept unconditionally (ADR 241 §9, arbitration R2).

ADR 241 §9 should now be read as listing `session_summary` with `entity_extraction` and `routine_weekly_review`. §10's reason applies to both session-text tasks.

### As-implemented surface

- `TASKS["session_summary"]`: `local_only=True`, description "Summarises a finished chat session (on-box only)."
- Tests in `orchestrator/tests/test_task_models.py`:
  - `test_session_summary_off_box_entry_resolves_to_code_default` (privacy `allow_cloud`/`local_only` × `user_id` `None`/`"u1"`, keyed cloud model with the key granted).
  - `test_session_summary_and_entity_extraction_share_the_on_box_rule`, which pins the two session-text tasks together.
- Reference manual: the background-task table now marks `session_summary` as on-box only for listed entries.

## Alternatives considered

- **Document why the summary may leave the box** (the issue's option (b)): not chosen. There is no difference in input between the two tasks to justify it, and the hardcoded `llama` before LUM-877 means no operator relied on cloud summaries.
- **A provenance gate** (`local_only` only when the session quoted email): not chosen. Chat messages carry no provenance today. This stays the revisit path for both tasks together, as ADR 241 already says for `entity_extraction`.
- **Leaving `temporal_extract` open**: first deferred as out of scope, then rejected before merge (2026-11-10, Thomas). The temporal tasks are `local_only` too; see Decision addendum below.

## Consequences

- The ADR 174 boundary now holds for the whole `session_end` job.
- An operator can no longer route chat summaries to a cloud model. The admin diagnostics view shows such an entry as `not_on_box`.
- Future chunks must know:
  - Any task that reads chat session text is `local_only`.
  - `session_summary` and `entity_extraction` change together.
  - Test fixtures that need a task that is not `local_only` use `relation_extraction`.
- **Closed before merge:** `temporal_extract` hydrates SESSION episodes from `sessions.summary` (`plugins/graph/temporal_pipeline.py`, `_HYDRATION_QUERIES`), and a summary can paraphrase or quote email. Both temporal tasks are now `local_only`: `temporal_extract` reads the summary and `temporal_judge` reads the facts extracted from it. This supersedes ADR 241 §9 for these two tasks, which kept them open only because DOCUMENT episodes have no hydration query and did not weigh SESSION summaries.

## Revisit conditions

- **A temporal task is ever loosened:** both temporal tasks read text that can quote email; loosening either needs a provenance check on SESSION episodes first.
- **Cloud summaries of ordinary chats:** if operators ask for them, add a provenance gate to `entity_extraction` and `session_summary` together. Do not loosen one of them alone.

## Linear linkage (Product OS)

- **Existing issue:** LUM-899
- **New issue needed:** no. The `temporal_extract` SESSION-summary path found in this run was fixed before merge (`fixed-now`).
- **Historical evidence only:** no

## Testing retrospective

- The new tests fail with the fix reverted: 6 fail (the registry row plus 5 new cases), 49 pass.
- Targeted pytest (task models, model enablement, LiteLLM loopback, memory, session-end stub): 250 passed. Admin diagnostics: 30 passed.
- Full orchestrator suite, run once in a throwaway container: 6377 passed, 278 skipped, 3 xfailed, and 9 environmental failures. Those are the git-worktree checks, which fail with "not a git repository" inside the container. `scripts/check-exec-bits.sh` passes on the host.
- Lint: ruff check and format, and the refresh-token jti guard, all pass.

## Status history

- 2026-11-10: Finalised by /record-retro (retrospective).
- 2026-11-16: Decision addendum (LUM-983) added by /verify-plan --headless. The 2026-11-10 addendum's rule for `temporal_judge` was not enforced; it is now. Draft: `.cursor/adrs/LUM-983-temporal_judge_on_box.md`. Amends ADR 241 §8.

## Decision addendum (2026-11-10, before merge)

On Thomas's decision, `temporal_extract` and `temporal_judge` are `local_only` as well, in the same branch. A listed off-box entry for either task is skipped (`not_on_box`) and the task falls back to its code default. Tests: `test_temporal_tasks_stay_on_box_because_session_summaries_feed_them`, `test_temporal_extract_off_box_entry_resolves_to_code_default`, and the updated registry table in `test_task_models.py`.

## Decision addendum (2026-11-16, LUM-983)

The 2026-11-10 addendum made `temporal_judge` `local_only`, but the resolver did not enforce it. ADR 241 §8 exempts a destructive task from every skip check, so `resolve_task_model` returned the judge's first listed entry unchecked. On an `allow_cloud` install, a cloud model listed first under `tasks.temporal_judge` received episode text: notes, voice memo transcripts and chat session summaries (`_HYDRATION_QUERIES`: `NOTE`, `SESSION`, `AUDIO`). The weekly code review found it.

**Decision.** For a destructive task that is also `local_only`, the first listed entry is used only when it is **proven on-box**: it is found in one `_ModelLookup`, and `is_on_box_local(name, cfg=cfg)` is true. Otherwise the task uses its code default (`resolved_from="default"`): that covers an off-box entry, a host that does not resolve, an entry that is not found, and a lookup or on-box check that raises. The task still never moves to a later entry. There is still no key, privacy or switched-off check, so an on-box entry that is keyless, blocked or switched off fails at the call site into `judge_failed` (ADR 241 §8). One private helper, `_destructive_choice` in `orchestrator/services/task_models.py`, encodes the rule for both `resolve_task_model` and `explain_task`, so admin diagnostics report the model actually used. `resolve_task_model` logs one warning per process per task and reason, then DEBUG for later substitutions; `explain_task` does not spend the warning.

**Alternatives not chosen** (`.cursor/explorations/LUM-983-temporal_judge_on_box.md`): gating only a proven off-box entry (an unknown dynamic alias could come back at the call site on a remote `OLLAMA_URL`); failing closed to `judge_failed` (contradicts the 2026-11-10 addendum and the `-> str` contract); a guard at the call site (diagnostics would stay wrong, and the rule would exist twice); the full skip check (would also swap keyless or blocked entries and read privacy per user).

**Consequences.**

- An unknown or misspelled first entry now runs the code default `llama` instead of ending `judge_failed`. The plugin's boot warning in `plugins/graph/__init__.py` now fires only when the default itself does not resolve.
- `llama` is also the ADR 151 reference judge. A configuration that used to give proposals only (a cloud or misspelled first entry) now auto-applies its verdicts when `LUMOGIS_TEMPORAL_AUTO_APPLY=true`. This is intended: `_run_episode_check` resolves the judge once and passes that name to both `judge_contradictions` and `apply_or_propose`, so the pin is checked against the model that judged. The result is the same as having no `tasks.temporal_judge` entry.
- A first entry that is only sometimes usable (an Ollama listing that times out, LAN DNS that fails) is decided per resolution: the operator's model when proven on-box, else `llama`. Before, those episodes ended `judge_failed` and were not retried.
- A LAN Ollama whose `base_url` does not contain `ollama` is refused, as for every other `local_only` task.
- Nothing is dialled for a substituted entry, so no `privacy_mode_block` audit row is written; the operator is told by boot check §3 (`task_model_unsatisfiable`), the warning, and admin diagnostics. `get_llm_provider`'s hard block for an explicit cloud model under `local_only` is unchanged.
- **Residuals, stated:** "on-box" is `is_on_box_local`'s `base_url` host check; it ignores `proxy_url` and the adapter, a weakness shared by every `local_only` task and the email boundary (folded into LUM-914 "Later"). The code default is not on-box checked, as for every task without `email_boundary` (ADR 241 §9); `temporal_extract` reads the same text first with the same default.

**Revisit.** If the ADR 151 eval fails `llama3.2:3b` (LUM-947), falling back to the default no longer means a measured judge, and failing closed should be reconsidered. A second destructive task that is not `local_only` keeps the unchecked first entry with no lookup.

**Tests:** in `orchestrator/tests/test_task_models.py`, `test_temporal_judge_off_box_entry_resolves_to_code_default`, `test_temporal_judge_never_takes_a_later_entry`, `test_destructive_task_returns_on_box_first_entry_unchecked`, `test_temporal_judge_on_box_keyless_entry_is_still_the_judge`, `test_temporal_judge_first_entry_on_unresolved_or_public_host_resolves_to_default`, `test_temporal_judge_lookup_error_resolves_to_default_without_leaking`, `test_temporal_judge_dynamic_alias_must_be_on_box`, `test_temporal_judge_default_warning_logged_once_and_not_by_explain`, `test_temporal_judge_warning_after_boot_call_is_per_reason`, `test_temporal_judge_follows_a_flapping_first_entry` and `test_destructive_task_that_is_not_local_only_returns_first_entry_with_no_lookup`; in `orchestrator/tests/test_task_models_call_sites.py`, `test_temporal_judge_cloud_entry_never_reaches_the_cloud_under_allow_cloud`, `test_temporal_judge_cloud_entry_is_replaced_and_explicit_cloud_is_still_blocked`, `test_temporal_judge_never_falls_through_its_list`, `test_judge_boot_warning_fires_when_the_default_is_unresolvable` and `test_substituted_judge_is_the_reference_and_may_auto_apply`; in `orchestrator/tests/test_api_v1_admin_diagnostics.py`, `test_admin_diagnostics_temporal_judge_cloud_entry_reads_default`. With the resolver change reverted, 23 test cases from this list fail (the positive controls pass).
