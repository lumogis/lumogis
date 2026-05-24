# ADR-061: Lumogis doctor — operator health CLI (v1)

**Status:** Finalised
**Created:** 2026-05-23
**Finalised:** 2026-05-23 (`/verify-plan` — LUM-199)
**Decided by:** Exploration + plan arbitration (`/explore`, `/review-plan` R1); implementation verified on product branch.

## Context

Operators need a single fast answer to *"is my Lumogis install healthy?"* that works **even when the orchestrator is down**. Existing surfaces (`make health` → `/health`, RC gates, raw `docker compose ps`) do not cover this. **LUM-178** (dashboard) and **LUM-184** (quickstart) consume the stable JSON contract from this chunk.

## Decision

Ship **`make doctor`** v1 as **pure shell + Docker primitives**: `Makefile` → `bash scripts/doctor/run.sh` with per-category scripts under `scripts/doctor/checks/` and `scripts/doctor/format.sh` for human and **v1 JSON** output (`scripts/doctor/schema.v1.json`). v1 is **read-only** for Lumogis data and deployment state (no `--fix`). **Security probes** (`audit-local`, **bandit**) are **opt-in** via `--security` / `LUMOGIS_DOCTOR_RUN_SECURITY=1`. Exit codes: **0** healthy, **1** warnings, **2** errors, **3** doctor infrastructure failure (`DOCTOR_FATAL:` on stderr; no JSON on stdout for exit 3).

## Consequences

- Stable JSON for **LUM-178** / **LUM-310**; consumers MUST tolerate unknown keys at the same top-level `version`.
- Human mode stays **`jq`‑free**; **`jq`** required only for `--json`.
- Tests: `orchestrator/tests/test_doctor_cli.py` stubs `docker`/`curl` via `PATH` and `LUMOGIS_DOCTOR_REPO_ROOT` for CI without live Docker.

## Revisit conditions

- Bump JSON **`version`** when breaking the contract; coordinate with LUM-178.
- **`--fix`** or in-process **`orchestrator.doctor`** — separate exploration/plan.
- Optional **`jq`‑free JSON** or CI **`make doctor`** against live compose — follow-up issues under LUM-199.

## Status history

- 2026-05-23: Draft in `.cursor/explorations/` / `.cursor/adrs/LUM-199-lumogis-doctor.md`.
- 2026-05-23: Finalised by `/verify-plan` — implementation matches decision; canonical copy here.
