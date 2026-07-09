# ADR-073: ingest_paths restart end-to-end verification approach (LUM-400)
**Status:** Finalised
**Created:** 2026-05-29
**Last updated:** 2026-05-29
**Decided by:** /explore --headless (claude-opus-4-8-thinking-medium)

> Status: Active
> Last reviewed: 2026-07-07
> Verified against commit: 6c80e10
> Notes: Allocated **`073-lum-400-*.md`** on merge to **`dev`** (after **070**–**072**; prefix **072** is a duplicate cluster — see **`072-lum-398-*.md`** and **`072-lum-401-*.md`**; prefix **074** is also duplicated — see **`074-lum-162-*.md`** and **`074-lum-178-*.md`**). Next free filename prefix for collision renames is **`158+`** (after **`157-lum-157-577-post-ship-sharing-fixes.md`**; prefixes **110**–**157** are now allocated — note three **154** and three **156** prefixes).

## Context
LUM-397 shipped multi-root `ingest_paths` settings, the `restart_required` banner, and `POST /settings/restart` (container recreate via the stack-control sidecar), all covered by unit/mock tests. `/verify-plan` accepted one **P1** gap: the Docker restart round-trip was never exercised on a live stack. LUM-400 is that follow-up — it must produce **reproducible evidence** that a restart truly re-reads a changed `ingest_paths`, the watcher activates on the new path, a dropped file is ingested and appears in search, and the malformed-`INGEST_PATHS` silent fallback is documented. The decisive constraint: adding a brand-new host directory needs a **new bind mount**, which Docker applies only on container **recreate** (`compose up --force-recreate`) — exactly what stack-control already does — so the verification must reproduce that real path, not a cheaper stand-in (e.g. `stop()/start()` of the same container) that tests a different mechanism.

## Decision
Verify the chain by **extending the existing in-repo pytest Compose integration suite** (`tests/integration/`, reusing its markers, session fixtures, `pytest.ini`, and the `scripts/integration-public-rc.sh` lifecycle), driving the **real `POST /settings/restart`** through the stack-control sidecar against a live RC/compose stack. Add a new opt-in `restart_e2e` marker so the disruptive recreate is isolated from the shared session `api` fixture. Wrap it with a thin Make target / `integration-public-rc.sh` subcommand for evidence capture, and add a short **manual runbook addendum** for the overlay-GUI leg (which stays manual until LUM-402). Prove ingest→search via `file_index_count` to avoid a hard Ollama dependency. No new dependencies; AGPL-3.0-only; fully local-first.

## Alternatives Considered
- **Dedicated bash E2E script (Option 2):** kept only as a thin evidence-capture wrapper; weaker assertions and duplicated lifecycle make it a poor primary deliverable.
- **testcontainers-python (Option 3):** rejected — adds a dependency and a parallel compose harness, and its `stop()/start()` restart reuses the same container config so it cannot add a new bind mount, i.e. it would not faithfully reproduce the production `--force-recreate` chain.
- **pytest-docker plugin (Option 4):** rejected — redundant with the existing harness; solves stack lifecycle, not the restart-and-reassert gap.
- **Manual runbook only (Option 5):** insufficient alone — not regression-proof or re-runnable for the orchestrator chain; retained only as the overlay-leg complement.

See `.cursor/explorations/LUM-400-ingest-paths-restart-e2e.md` for full detail.

## Consequences
- **Easier:** a re-runnable artefact (pytest + captured log) that exercises the exact shipped restart path and that `/verify-plan` can cite for closure; no new deps; stays local-first/AGPL.
- **Harder / constraints:** the test recreates the orchestrator mid-run, so it must be isolated behind its own marker and needs a robust post-recreate ready-poll; the overlay GUI leg remains manual until LUM-402.
- **Future chunks must know:** multi-bind `ingest_paths[1..n]` live verification depends on the LUM-401 compose generator — LUM-400 proves the index-0/within-mounted-volume case now and defers the brand-new-bind-mount case with a Linear note. This commits restart-class verification to the bespoke compose harness (no testcontainers/pytest-docker for this class).

## Revisit conditions
- If Lumogis adopts testcontainers/pytest-docker elsewhere and the bespoke `integration-public-rc.sh` harness is retired, revisit whether this test should migrate.
- If stack-control changes its restart mechanism away from `compose up --force-recreate`, re-validate that the test still exercises the production path.
- **LUM-401** shipped (**`docs/decisions/072-lum-401-compose-multibind-generator.md`**, `orchestrator/compose_ingest_binds.py`). Extend restart E2E to multi-bind `ingest_paths[1..n]` when scheduled; index-0 coverage remains LUM-400.
- If `get_effective_ingest_paths()` gains a user-visible error for malformed `INGEST_PATHS` (instead of silent fallback), update the negative test's expected behaviour.

## Implementation note (verify-plan, 2026-05-29)
The decision held: the chain is verified by `tests/integration/test_ingest_paths_restart_e2e.py` (opt-in `restart_e2e` marker) driving the real `POST /settings/restart` → stack-control `--force-recreate` against the RC compose stack, proving ingest via `file_index_count` (no Ollama dependency). The `make e2e-ingest-restart` gate passes (3 tests: admin-401, malformed-fallback, happy-path remount-and-ingest).

**Deviation discovered — verification surfaced real product defects.** The plan scoped this as "verification only, no product behaviour change", but the live chain did not work until three product/harness fixes were made (intent-preserving — they make the verified restart chain actually function):
1. **`orchestrator/main.py`** — startup crash-recovery reclaim of orphaned `running` batch jobs (`reset_stuck(stuck_after_seconds=0)` at boot). Without it, a mid-ingest `--force-recreate` orphans the worker's job and the per-user concurrency cap head-of-line-blocks re-ingest for up to `BATCH_QUEUE_STUCK_AFTER_SECONDS` (~30 min). This is a **production behaviour change** (safe under Lumogis' single-orchestrator-instance assumption — documented inline).
2. **`stack-control/main.py`** — honour `HOST_PROJECT_DIR` so an in-container `docker compose --force-recreate` resolves relative bind-mount sources to real host paths (default `/project` preserves historical behaviour).
3. **`orchestrator/docker-entrypoint.sh`** — `OLLAMA_SKIP_WAIT=true` short-circuits the on-boot Ollama readiness wait + model pull for a fast, deterministic recreate (default-off preserves production behaviour).

These were not in an ADR of their own; this note records them. The single-instance batch-queue reclaim in particular warrants a tracked follow-up (see plan Implementation Log).

## Status history
- 2026-05-29: Draft created by /explore --headless (LUM-400)
- 2026-05-29: Finalised by /verify-plan — implementation confirmed the test-approach decision; recorded product-fix deviations surfaced by the live verification (see Implementation note).
