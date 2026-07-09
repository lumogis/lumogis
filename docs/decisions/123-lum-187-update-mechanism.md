# ADR-123: Docker Compose update mechanism — version check + scripts (LUM-187)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-30

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-187](https://linear.app/lumogis/issue/LUM-187)

**Related:** [ADR-098](098-lum-185-backup-restore.md) (rollback needs recent backup); [ADR-147](147-lum-524-admin-update-banner.md) (LUM-524 admin UI banner — shipped 2026-06-30)

## Context

Persona A/B Docker installs need an operator update path: version visibility, image pull/restart, migration preview, rollback with backup guard. LUM-396/408 track native Server updater separately.

## Decision

1. **Version check (read-only):** `orchestrator/services/update_check.py` compares `__version__` to latest GitHub release (PEP 440, fail-soft). **`GET /api/v1/admin/diagnostics/update-status`** (admin-gated).
2. **Migration preview:** `db_migrations.py --dry-run`; **`make migrate-dry-run`**.
3. **Operator scripts:** `scripts/update/update.sh` (pull + restart + `/healthz` gate), `scripts/update/rollback.sh` (re-pin images; requires backup within `LUMOGIS_BACKUP_MAX_AGE_HOURS`, default 24h).
4. **Docs:** `docs/LUMOGIS_REFERENCE_MANUAL.md` update-mechanism paragraph.

**Not shipped:** live `make update` prove in CI; native Server auto-update. Admin SPA banner shipped **LUM-524** ([ADR-147](147-lum-524-admin-update-banner.md)).

## Consequences

- **Easier:** Operators can check version and run documented update/rollback on Compose installs.
- **Harder:** Rollback is forward-migration-limited; backup discipline required.

## Status history

- 2026-06-22: Finalised by `/record-retro` — backend slice on `dev` @ `c1d30c1d4`.
- 2026-06-30: Cross-reference updated by `/verify-plan` LUM-524 — admin SPA banner no longer deferred ([ADR-147](147-lum-524-admin-update-banner.md)).
