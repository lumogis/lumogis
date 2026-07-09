# ADR 125: Amendment to ADR 123 — rollback image pinning + migration health gate (LUM-187)

**Status:** Finalised

**Created:** 2026-06-24

**Last updated:** 2026-06-24

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-24 (Composer)

**Plan:** none — shipped via Cursor bug-fix branch before formal plan / verify for this slice

**Exploration:** `.cursor/explorations/lum_187_rollback_pin_migration_gate_retro.md`

**Draft mirror:** `.cursor/adrs/lum_187_rollback_pin_migration_gate.md`

**Amends:** `docs/decisions/123-lum-187-update-mechanism.md` (operator scripts: rollback pinning + post-health migration verification)

**Issue:** [LUM-187](https://linear.app/lumogis/issue/LUM-187)

**Related:** [ADR-098](098-lum-185-backup-restore.md) (rollback backup guard unchanged); [ADR-147](147-lum-524-admin-update-banner.md) (LUM-524 admin Software updates card — shipped 2026-06-30)

## Context

**ADR 123** (retro **2026-06-22**, `dev` @ `c1d30c1d4`) shipped the Docker Compose update mechanism: version check API, `make migrate-dry-run`, `scripts/update/update.sh` (pull + restart + `/healthz`), and `scripts/update/rollback.sh` (re-pin images with backup guard).

Two correctness gaps remained on **`dev`** after that retro:

1. **Rollback did not pin images** — `rollback.sh` pulled captured digests but ran `docker compose up -d` without changing compose image refs, so GHCR installs using floating `:latest` tags could not roll back to the pre-update digests.
2. **Update health gate was shallow** — `update.sh` (and rollback) treated `/healthz` success as sufficient; they did not verify migrations applied or detect migration runner errors in orchestrator boot logs.

Fix landed via **`cursor/critical-bug-investigation-add0`** (commit **`1bcf79fec`**, merged **`c465a1668`**, 2026-06-24) without a Product OS plan/verify loop.

## Decision

1. **Shared helpers** — `scripts/update/common.sh` factors `capture_rollback_state`, `compose_with_rollback_override`, and `wait_for_stack_ready` used by both update and rollback scripts.
2. **Per-service rollback state** — before pull, `update.sh` records `service<TAB>image<TAB>ref` (digest when available) from **running** containers into `{LUMOGIS_UPDATE_STATE_DIR}/previous-images.txt` (default `.lumogis-state/`).
3. **Compose override for rollback** — `scripts/update/write_rollback_override.py` writes `rollback-compose.override.yml` with per-service `image:` pins and `build: !reset null`; `rollback.sh` appends this file to **`COMPOSE_FILE`** before `docker compose up -d`.
4. **Migration health gate** — after `/healthz` passes, `wait_for_stack_ready` runs `db_migrations.py --dry-run` inside the orchestrator container, fails on dry-run error or pending migrations, and fails if recent orchestrator logs contain migration runner WARNING/ERROR patterns.
5. **Unit tests** — `orchestrator/tests/test_write_rollback_override.py` covers TSV parse, YAML render, round-trip write, and CLI.

**ADR 123** remains the canonical record for version check, migration preview, and the original operator-script contract; this ADR **narrows** update/rollback script behaviour for GHCR rollback correctness and migration verification.

**Not shipped (unchanged from ADR 123):** live `make update` prove in CI; native Server auto-update (**LUM-396** / **LUM-408**). Admin SPA Software updates card shipped **LUM-524** ([ADR-147](147-lum-524-admin-update-banner.md)).

## Alternatives considered

- **Pull digests only, no compose override** (ADR 123 as-shipped rollback) — rejected; `:latest` compose refs ignore pulled digests on `up -d`.
- **`/healthz` only post-restart gate** — rejected; health can pass while migrations pending or boot logged migration failures.
- **Edit ADR 123 in place** — rejected; Lumogis record-retro convention is amendment ADRs (see **ADR 111** amending **ADR 109**).

## Consequences

- **Positive:** GHCR Persona A operators get deterministic rollback to captured digests; failed or incomplete migrations fail loudly at end of `make update` / `make rollback`.
- **Limits:** Migration gate requires a running orchestrator container (`docker compose exec`); no shell-level integration test in this slice; **`docs/LUMOGIS_REFERENCE_MANUAL.md`** update-mechanism paragraph may still describe pre-override wording until a doc pass under **LUM-187**.

## Revisit conditions

- Compose multi-file / build-context profiles break override pinning — add integration test.
- `db_migrations.py --dry-run` output format changes — update `wait_for_stack_ready` grep.
- Operator reports rollback still floats tags — inspect `COMPOSE_FILE` append order and override generation.

## Linear linkage (Product OS)

- **LUM-187:** parent update-mechanism issue — post-ship hardening (comment via **`/linear-update`**, not a new issue required).
- **New issue needed:** no

## Testing retrospective

| Layer | Command / artefact | Result |
|-------|-------------------|--------|
| Unit | `cd orchestrator && AUTH_ENABLED=false python3 -m pytest tests/test_write_rollback_override.py -q` | **6 passed** (2026-06-24, local venv) |
| Integration | live `make update` / `make rollback` | **Not run** |

Full **`make test`** not re-run for this retro slice.

## Status history

- 2026-06-24: Finalised by `/record-retro` — amendment on `dev` @ `35eb534cb` (includes merge `c465a1668`).
