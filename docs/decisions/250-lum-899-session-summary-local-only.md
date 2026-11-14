# ADR-250: Chat session summaries stay on the box (LUM-899)
**Status:** Finalised
**Created:** 2026-11-10
**Last updated:** 2026-11-10
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-10 (claude-opus-5-5)
**Plan:** none. This shipped before a formal plan / verify cycle for the slice (`exec:claude` session).
**Exploration:** `.cursor/explorations/session_summary_local_only_retro.md`
**Draft mirror:** `.cursor/adrs/session_summary_local_only.md`
**Linear:** LUM-899
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

## Decision addendum (2026-11-10, before merge)

On Thomas's decision, `temporal_extract` and `temporal_judge` are `local_only` as well, in the same branch. A listed off-box entry for either task is skipped (`not_on_box`) and the task falls back to its code default. Tests: `test_temporal_tasks_stay_on_box_because_session_summaries_feed_them`, `test_temporal_extract_off_box_entry_resolves_to_code_default`, and the updated registry table in `test_task_models.py`.
