# ADR-274: `make backup` runs in the running backup container, and `make rollback` keeps the `.env` overlays (LUM-990)

**Status:** Finalised
**Created:** 2026-11-18
**Last updated:** 2026-11-18
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-18 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/update_backup_rollback_overlays_retro.md`
**Draft mirror:** `.cursor/adrs/update_backup_rollback_overlays.md`
**Linear:** LUM-990
**Amends:** [ADR 268](268-lum-984-rollback-image-tags.md) (how the rollback override is added to `COMPOSE_FILE`; the backup step before `make update`)

## Context

LUM-990 was the live end-to-end proof that ADR 268's rollback tags work on a full stack (plan MS-1). Run on the lumogis-dev host (Docker 29.9.2, containerd image store) on 2026-11-18, it found two failures that the unit, fake-docker and real-daemon tests could not show:

1. `make backup` ran `docker compose run --rm backup …`. On a checkout the backup service has `pull_policy: build`, so that rebuilt the backup image under the same tag. On the containerd store the image record of the still-running `backup` container disappeared, and `make update` stopped: "could not keep the running backup image … No such image". The upgrade guide's own order (back up, then update) hit this every time.
2. `compose_with_rollback_override` exported `COMPOSE_FILE=docker-compose.yml:<override>` whenever the shell had no `COMPOSE_FILE`. An exported value overrides `.env`, where households set their overlays (published images, GPU, graph), so `make rollback` started the previous images without them. In the run, the old orchestrator lost the overlay that points it at the `postgres` service and crash-looped.

## Decision

- **`make backup`** runs `docker compose exec -T backup /scripts/backup/backup.sh run` when the backup container is running, and `docker compose run --rm backup …` only when it is not. A one-off command against a long-running service on a checkout must not rebuild that service's image under its running tag.
- **`compose_with_rollback_override`** starts from the `COMPOSE_FILE` Compose itself would use: the shell value, else the `.env` value, else `docker-compose.yml`. The separator follows `COMPOSE_PATH_SEPARATOR` the same way (default `:`). `.env` is read by `_dotenv_value` in `scripts/update/common.sh`: the last `KEY=` line wins, an `export ` prefix is allowed, surrounding quotes are stripped, and an unquoted value drops a ` #` comment.

### As-implemented surface

- `Makefile` target `backup:`
- `scripts/update/common.sh`: `_dotenv_value`, `compose_with_rollback_override`
- `orchestrator/tests/test_update_rollback_compose_env.py` (12 F-tests)

## Alternatives considered

Not chosen at ship time:

- **`docker compose run --pull never` / `--no-build` for `make backup`.** Compose 5.6 has no `--no-build` on `run`, and `pull_policy: build` on the service still builds; `exec` avoids the build entirely.
- **Skipping the backup service in `capture_rollback_state` when its image is gone.** It would let the update continue but leave the backup service without a rollback image; fixing the cause keeps every service rollback-capable.
- **Making `rollback.sh` source `.env`.** Sourcing would also import unrelated keys and run shell syntax from a user file; reading the two keys Compose needs is narrower.

## Consequences

- The documented order `make backup` → `make update` → `make rollback` works on a checkout with the containerd image store and overlays in `.env`. Proven live: LUM-990 MS-1 run 3 (`ROLLBACK-OK`, `MARKER-CLEARED`, no workarounds).
- `make backup` while the backup container runs shares the `.backup.lock` with the nightly job inside the same container.
- Release installs are unchanged: their images are pulled, so `docker compose run --rm backup …` in the upgrade guide does not rebuild, and they have no rollback scripts (ADR 262).
- Any future script that exports `COMPOSE_FILE` must start from `_dotenv_value`, not a bare default.

## Revisit conditions

- Another script under `scripts/` exports `COMPOSE_FILE` or `COMPOSE_PROFILES`.
- Compose `.env` parsing that `_dotenv_value` does not cover becomes relevant (multi-line values, `${VAR}` interpolation inside `COMPOSE_FILE`).
- The backup service stops being a long-running container.

## Linear linkage (Product OS)

- **Existing issue:** LUM-990 (P1 `gate:pre-main` child of LUM-984), Done on 2026-11-18 with the MS-1 evidence in its comments.
- **New issue needed:** no.
- **Historical evidence only:** no.

## Testing retrospective

- 12 new F-tests; with `test_rollback_image_tags.py`: 29 passed, 4 skipped (the D-tests need a Docker daemon, absent in the test container).
- shellcheck v0.10.0 clean on `scripts/update/common.sh`.
- `test_deployment_hardening_config.py::test_stop_entities_default_matches_the_repair_list` fails on `dev` with and without this change (unrelated).
- Gaps: the D-tests, the classic image store and macOS were not exercised; full `make test` was not run.

## Status history

- 2026-11-18: Finalised by /record-retro (retrospective).
