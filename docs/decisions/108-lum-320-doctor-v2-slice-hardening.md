# ADR-108: Doctor v2 slice hardening — allowlist, jq, Makefile, tests, restart guard (LUM-340 / 337 / 343 / 344 / 494)

**Status:** Finalised  
**Created:** 2026-06-19  
**Last updated:** 2026-06-19  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-06-19 (Composer)  
**Plan:** none for this bundle — shipped on `claude/youthful-carson-84uh2i`  
**Exploration:** `.cursor/explorations/doctor_v2_slice_hardening_retro.md`  
**Draft mirror:** `.cursor/adrs/doctor-v2-slice-hardening.md`  
**Builds on:** ADR-065 (LUM-320 doctor v2), ADR-061 (LUM-199 doctor v1), ADR-063 (LUM-319 CI)

## Context

Follow-on doctor work bundled on the same branch as the web UX cluster: versioned core-service allowlist, image `jq` for JSON contract tests, Makefile shortcuts, LUM-320 test-case backfill, and restart-loop guard for `compose_restart_service`.

**LUM-341** (`.env` safelist / `set_env_key`) is recorded separately via `/verify-plan` and ADR-065 amendment — not duplicated here.

## Decision

| Ticket | As-shipped |
| --- | --- |
| **LUM-340** | `scripts/doctor/core-services.json` manifest + `load_core_allowlist()` precedence (mirrors env-safelist pattern) |
| **LUM-337** | `jq` in `orchestrator/Dockerfile` so `make compose-test-doctor` / JSON shape checks run in CI image |
| **LUM-343** | Makefile targets wrapping `doctor --fix` flows |
| **LUM-344** | Backfill LUM-320 §Test cases in plan/ADR evidence |
| **LUM-494** | Restart-loop guard: only **applied** restart rows count toward loop limit; dedicated pytest |

## Alternatives considered

- Fold all into one mega-ticket — rejected (Linear already tracks slice ids).

## Consequences

- Doctor JSON contract tests depend on `jq` in the orchestrator image.
- Restart guard prevents runaway `compose_restart_service` apply loops.

## Revisit conditions

- Change `K`/`S` allowlist semantics → update manifest + ADR-065 cross-ref.
- New repair kinds → extend `schema.v2.json` and `test_doctor_cli.py` harness.

## Linear linkage (Product OS)

- **LUM-340**, **LUM-337**, **LUM-343**, **LUM-344**, **LUM-494** — close after merge via `/linear-update`.

## Testing retrospective

- **19 passed** carson-filtered doctor/health pytest subset in orchestrator container (`set_env_key`, restart_loop, health).
- **5 failures** on full `test_doctor_json_schema_version` / `test_doctor_exit_code_all_ok` when run inside `docker compose run` against live `lumogis-test` project — **environmental** (`BACKUP_HOST_DIR` warn → exit 1), not branch regressions.
- **`make compose-test-doctor`** — failed here due to `lumogis-test` network conflict after partial teardown; re-run on clean host before merge.

## Status history

- 2026-06-19: Finalised by `/record-retro`.
