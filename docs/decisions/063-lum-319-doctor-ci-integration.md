# ADR-063: Dockerised CI integration for `make doctor` on `lumogis-test` (LUM-319)

**Status:** Finalised
**Created:** 2026-05-23
**Finalised:** 2026-05-23 (`/verify-plan --headless` — LUM-319)
**Decided by:** `.cursor/explorations/LUM-319-doctor-ci-integration.md` + `.cursor/plans/LUM-319-doctor-ci-integration.plan.md`; implementation verified on **`agent/lum-319`**.

## Context

`make doctor` v1 (**ADR 061-lum-199**, LUM-199) is exercised in default PR CI mainly via stubbed pytest (`orchestrator/tests/test_doctor_cli.py`). LUM-319 closes the gap with a **live** compose smoke on the **`lumogis-test`** stub stack so regressions in `scripts/doctor/run.sh`, checks, and the v1 JSON envelope surface before merge.

## Decision

1. **`Makefile` target `compose-test-doctor`** — Materialises root **`.env`** from **`config/test.env.example`**, exports **`COMPOSE_PROJECT_NAME=lumogis-test`** and a **two-file** **`COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml`** (shell overrides the three-file default inside the copied `.env` so bare `docker compose` in `run.sh` does not pull **`docker-compose.public-rc-stack.yml`**), runs **`docker compose --env-file config/test.env.example up -d --wait --wait-timeout 180`**, then **`$(MAKE) doctor ARGS="--json"`** redirected to **`doctor.json`**, asserts **`jq -e '.version == 1 and (.checks | type == "array")'`**, removes **`doctor.json`** on success, and **`docker compose … down -v`** via **`trap`** on exit. Does **not** set **`LUMOGIS_DOCTOR_RUN_SECURITY=1`** (security category stays skipped).

2. **Path script `.github/scripts/doctor-integration-paths.sh`** — Same contract as other path gates: **`push`** → always run; **`pull_request`** → diff against listed paths; other events → exit **1**.

3. **GitHub Actions job `doctor-integration`** in **`.github/workflows/ci.yml`** — Runs **`make compose-test-doctor`** when the path gate matches; optional **`jq`** install fallback; uploads **`doctor.json`** when present (**`if-no-files-found: ignore`**); bounded **`docker compose ps` / `logs`** on **`always()`** for diagnostics.

4. **Operator docs** — **`scripts/doctor/README.md`** §CI parity points at this ADR/plan behaviour.

## Consequences

- PRs touching doctor / compose / Makefile / CI wiring pay the integration cost (~path-gated); operators reproduce CI with **`make compose-test-doctor`**.
- A bump to doctor JSON **`version`** must update the **`jq`** line in **`compose-test-doctor`** in lockstep with **ADR 061** consumers.
- **`Makefile`** sets **`SHELL := /bin/bash`** so recipes using **`set -o pipefail`** (notably **`compose-test-doctor`**) work on Debian/Ubuntu where **`/bin/sh`** is **dash**.

## Relationship to ADR-061

Implements the **ADR 061-lum-199** *revisit* slice for optional **CI `make doctor` against live compose** (child **LUM-319**), without moving doctor into **`make verify-public-rc`**.

## Status history

- 2026-05-23: Draft in `.cursor/adrs/LUM-319-doctor-ci-integration.md` (exploration Option D).
- 2026-05-23: Finalised by `/verify-plan --headless` — implementation matches decision; canonical copy here.
