# ADR 171: Capability permission scopes — enforce `permissions_required` (LUM-612)

**Status:** Finalised
**Created:** 2026-07-16
**Last updated:** 2026-07-16
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-16 (Composer)
**Plan:** none — shipped on `claude/lum-612-capability-permission-grant` before formal plan archive
**Exploration:** `.cursor/explorations/lum_612_capability_permission_scopes_retro.md`
**Draft mirror:** `.cursor/adrs/lum_612_capability_permission_scopes.md`
**Linear:** [LUM-612](https://linear.app/lumogis/issue/LUM-612) (LUM-507 pillar a; parent LUM-507)

## Context

ADR-170 decomposed LUM-507 into pillars **(a)** permission/grant, **(b)** sandbox/egress, **(c)** signing. Pillar **(a)** makes `CapabilityManifest.permissions_required` real: ADR-169 declares scopes on the manifest, but until this chunk Core only enforced binary Ask/Do per connector.

## Decision

Ship least-privilege **scope enforcement** at the capability invoke chokepoint:

- Migration **052** adds `scopes TEXT[]` to `connector_permissions` (ADR-024 reserved column).
- Permission identity for a capability is connector **`capability.{manifest.id}`** (`orchestrator/services/capability_scopes.py`).
- `ToolExecutor.execute_capability_http` denies when any required scope is missing, returning structured `missing_scopes` before HTTP (`orchestrator/services/execution.py`).
- Grant/revoke via **`PUT /api/v1/me/permissions/{connector}`** with optional `scopes` array (`routes/connector_permissions.py`).
- Registry refuses manifests with malformed required scopes or ungrantable capability ids (`services/capability_registry.py`).

### Invariants

- Ungranted scope → deny (fail-closed), even if binary Ask/Do allows.
- Empty `permissions_required` → scope gate skipped (legacy manifests with `[]`).
- Scope strings must match `area:verb` pattern so declared scopes are grantable.

## Alternatives considered

- Full policy engine (Cedar/OPA) now — deferred per ADR-024; extend Ask/Do with scopes first.
- Enforce only at registration — rejected; invoke chokepoint is the security boundary.

## Consequences

- External capability authors can declare scopes in ADR-169 manifests and expect enforcement.
- Grant UI/API can show exact scope lists at install time.
- P1 gap: HTTP route-contract tests for dotted `capability.{id}` paths → **LUM-615**.

## Revisit conditions

- LUM-615 closes route-contract gap.
- Scope cardinality or principal model outgrows Ask/Do → revisit policy engine.

## Linear linkage (Product OS)

- **Shipped under:** LUM-612 (Done after merge @ `8b979bfc8`)
- **P1 follow-up:** LUM-615 (HTTP route-contract tests)
- **Parent:** LUM-507 remains open (pillars b partial, c unsigned)

## Testing retrospective

- **Tests:** `test_capability_scopes.py`, `test_capability_permission_enforcement.py`; matrix row **1.6.11**
- **Verified:** 53 targeted pytest passed on merge (2026-07-16)
- **Gap:** LUM-615 end-to-end FastAPI route tests

## Status history

- **2026-07-16:** Finalised by /record-retro (as-shipped on `claude/lum-612` → `dev`).
