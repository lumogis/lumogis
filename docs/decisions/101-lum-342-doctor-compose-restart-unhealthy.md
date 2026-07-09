# ADR-101: Doctor `compose_restart_service` for unhealthy containers (LUM-342)

**Status:** Finalised
**Created:** 2026-06-15
**Finalised:** 2026-06-15 (`/verify-plan` — **LUM-342**)
**Decided by:** `/explore LUM-342`; plan arbitration R1; implementation verified against `LUM-342-doctor-compose-restart-unhealthy.plan.md`. Parent context: **ADR-061** / **ADR-065** (LUM-320 slice 1).

## Context

LUM-320 slice 1 (`--fix`) repairs **stopped** compose services via `compose_up_service` and deferred **`docker compose restart` for unhealthy running containers** (ADR 061). Operators with flapping healthchecks saw **error** rows and logs-only remediation. LUM-342 closes that gap under the same host-side, orchestrator-independent doctor invariant.

**ADR 074** requires LUM-342 to own restart UX before Lumogis Web adds in-panel restart controls. Dashboard slice 1 remains read-only.

## Decision

Add a fourth safelisted repair kind **`compose_restart_service`**: when `docker compose ps` reports **`State=running`** and **`Health=unhealthy`**, `services.sh` emits a 7-field fix row; **`repair.sh`** validates the service ∈ **`K_CORE ∩ S`** (same frozen allowlist as `compose_up_service`) and, on **`--fix --apply --yes`**, runs **`timeout … docker compose restart <service>`** with NDJSON audit. Read-only doctor remains the default; no new CLI flags beyond the existing **`--fix`** / **`--apply --yes`** contract. **Do not** fold unhealthy restart into `compose_up_service`.

**Planner divergence from slice 1:** For `compose_restart_service`, service ∉ **S** maps to **`error`** (not **`skipped`** as for `compose_up_service`) because the check row observed the container **running** — S-miss is anomalous. K-miss still maps to **`skipped`** (`allowlist` message).

## Alternatives considered

- **Extend `compose_up_service`** — rejected: wrong state gate and heavier semantics (`up -d` vs `restart`).
- **Second env opt-in (`LUMOGIS_DOCTOR_ENABLE_RESTART`)** — rejected; `--fix` already default-off.
- **Autoheal sidecar / external cron** — rejected: new Docker service, splits audit trail.
- **Dashboard-only restart** — rejected: violates ADR 074 sequencing and doctor-down invariant.

## Consequences

**Easier:** Operators recover from healthcheck flaps without manual `docker compose restart`; structured v2 JSON `repairs[]` and audit match slice 1; future dashboard restart can link to documented doctor path.

**Harder / constrained:** Restarting stateful services (Postgres, Qdrant, FalkorDB) causes brief outage — same blast radius as manual restart; README documents operator warnings (inspect logs before `--apply`; restart does not fix disk/volume faults). **LUM-340** may later replace hardcoded **K** with a manifest.

**Future chunks must know:** Lumogis Web restart buttons stay out until LUM-342 verify ships; **stack-control** restart API remains a separate authenticated surface for in-app use later.

## Revisit conditions

- Operator reports restart loops on unhealthy DBs → consider excluding stateful services from restart kind only, or add restart-loop guard (deferred P2 follow-up).
- **LUM-340** ships compose-derived allowlist → migrate validation to shared manifest without changing argv shape.
- Demand for in-dashboard restart → reconcile with ADR 074; reuse same **K** and audit semantics.

## Status history

- 2026-06-15: Draft created by `/explore` (LUM-342).
- 2026-06-15: Revised during `/review-plan --arbitrate` R1 — S-miss → `error` divergence; stateful DB acceptance.
- 2026-06-15: Finalised by `/verify-plan` — implementation confirmed; canonical copy here.
