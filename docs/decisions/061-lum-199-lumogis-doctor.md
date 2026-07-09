# ADR-061: Lumogis doctor — operator health CLI (v1)

**Status:** Finalised
**Created:** 2026-05-23
**Finalised:** 2026-05-23 (`/verify-plan` — LUM-199)
**Decided by:** Exploration + plan arbitration (`/explore`, `/review-plan` R1); implementation verified on product branch.

## Context

Operators need a single fast answer to *"is my Lumogis install healthy?"* that works **even when the orchestrator is down**. Existing surfaces (`make health` → `/health`, RC gates, raw `docker compose ps`) do not cover this. **LUM-178** (dashboard) and **LUM-184** (quickstart) consume the stable JSON contract from this chunk.

## Decision

Ship **`make doctor`** v1 as **pure shell + Docker primitives**: `Makefile` → `bash scripts/doctor/run.sh` with per-category scripts under `scripts/doctor/checks/` and `scripts/doctor/format.sh` for human and **v1 JSON** output (`scripts/doctor/schema.v1.json`). v1 is **read-only** for Lumogis data and deployment state when **`--fix`** is not used (LUM-320 adds an opt-in **`--fix`** / **`repair.sh`** path; see amendment below). **Security probes** (`audit-local`, **bandit**) are **opt-in** via `--security` / `LUMOGIS_DOCTOR_RUN_SECURITY=1`. Exit codes: **0** healthy, **1** warnings, **2** errors, **3** doctor infrastructure failure (`DOCTOR_FATAL:` on stderr; no JSON on stdout for exit 3), **4** refusal (`DOCTOR_REFUSED:`) for disallowed **`--fix --apply`** combinations (see amendment).

## Consequences

- Stable JSON for **LUM-178** / **LUM-310**; consumers MUST tolerate unknown keys at the same top-level `version`.
- Human mode stays **`jq`‑free**; **`jq`** required only for `--json`.
- Tests: `orchestrator/tests/test_doctor_cli.py` stubs `docker`/`curl` via `PATH` and `LUMOGIS_DOCTOR_REPO_ROOT` for CI without live Docker.

## Revisit conditions

- Bump JSON **`version`** when breaking the contract; coordinate with LUM-178.
- **Shell `--fix` remediation** — **LUM-320** slice 1 (see amendment below); distinct from in-process probe deferral below.
- **In-process `orchestrator.doctor` (LUM-322):** exploration **LUM-322** concluded to **defer** a dedicated `python -m orchestrator.doctor` (or `orchestrator/doctor/`) parallel to shell v1 until measurable gates fire. Reopen only if one of the following holds:

  1. **≥ 3 operator support cases** within a single release where shell `make doctor` reports `ok` **but** the orchestrator is misbehaving in a way `/admin/health` does not yet probe (record the specific probe gap each time).
  2. **LUM-178** (dashboard) ships and identifies a probe class that **must not** run inside the orchestrator process (e.g. for blast-radius reasons) and cannot be expressed in `docker`/shell.
  3. **LUM-320** (`--fix`) ships and post-ship evidence shows operators consistently want a deep **pre-fix** probe pass that shell v1 cannot produce.
  4. **`/admin/health` extension (Option B)** is attempted and proven insufficient — i.e. an in-process probe is desired but cannot live behind the existing route for documented architectural reasons.

  **Supersedence:** If **none** of the above fires within the next **two operator-facing milestones**, treat **LUM-322** as **superseded** by `/admin/health` + shell v1 + shell v2 `--fix`.

  **Preferred order if a trigger fires:** extend **`GET /admin/health`** first; only consider `python -m orchestrator.doctor` if that path is documented insufficient.

- Optional **`jq`‑free JSON** or CI **`make doctor`** against live compose — follow-up issues under LUM-199.

## Status history

- 2026-05-23: Draft in `.cursor/explorations/` / `.cursor/adrs/LUM-199-lumogis-doctor.md`.
- 2026-05-23: Finalised by `/verify-plan` — implementation matches decision; canonical copy here.
- 2026-05-24: Amendment — v2 `--fix` slice 1 verified (`/verify-plan` — **LUM-320**); canonical **`docs/decisions/065-lum-320-doctor-v2-shell-fix-remediation.md`**.
- 2026-05-29: Amendment — audit NDJSON size-based rotation + retention env vars (**LUM-338**); see **Audit** bullet above and **`scripts/doctor/README.md`** § Audit log retention.
- 2026-05-29: Amendment — concurrent **`--fix --apply`** flock guard (**LUM-399**): apply-only non-blocking **`flock -n`** on **`{LUMOGIS_DOCTOR_AUDIT_DIR}/repair.lock`** in **`repair.sh`** before Python; second caller **`DOCTOR_REFUSED:`** exit **4**.
- 2026-05-24: LUM-322 — Revisit conditions expanded (defer in-process `orchestrator.doctor` until ADR-061 gates; prefer extending `GET /admin/health` first).
- 2026-06-19: Amendment — core-service allowlist (**K**) externalised to a versioned manifest **`scripts/doctor/core-services.json`** (**LUM-340**). `repair.sh` loads it (override via **`LUMOGIS_DOCTOR_CORE_SERVICES_FILE`**, then script-local, then repo-relative) and falls back to an in-script copy if missing/invalid; a malformed manifest can never widen K. **Set is unchanged** (no behaviour change) — this is maintainability/versioning only. See **`scripts/doctor/README.md`** § Core-service allowlist.
- 2026-06-19: Amendment — restart-loop guard for **`compose_restart_service`** (**LUM-494**): `repair.sh` counts **applied** restart rows per service in `repair.ndjson` and emits **`skipped`** (not a mutation) once **`LUMOGIS_DOCTOR_RESTART_LOOP_MAX`** (default 3) is reached within **`LUMOGIS_DOCTOR_RESTART_LOOP_WINDOW_SEC`** (default 3600); active in dry-run and apply. See **`scripts/doctor/README.md`** § Restart-loop guard.
- 2026-06-19: Pointer — slice-2 `.env` config-edit safelist **implemented** (**LUM-341**): deny-by-default, append-only, non-secret, opt-in (`LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1`) `set_env_key` repair kind + `env-safelist.json` manifest. Full threat model + decision + rollback in **ADR-065** § Amendment — slice 2; see **`scripts/doctor/README.md`** § `.env` config-edit safelist.

## Amendment — v2 `--fix` (2026-05-24, LUM-320 slice 1)

Read/write remediation ships as an **opt-in** shell path: `make doctor ARGS="--fix"` (dry-run by default), `ARGS="--fix --apply --yes"` for non-interactive mutation, optional explicit `ARGS="--fix --dry-run"`. **`--dry-run` wins over `--apply`** when both appear (stderr warning; no mutations).

- **JSON:** `make doctor ARGS="--json --fix"` emits **`version: 2`** (`scripts/doctor/schema.v2.json`) with top-level **`apply_requested`**, **`any_applied`**, and intent helper **`dry_run`** (`dry_run := !apply_requested` in slice-1 emitters). Plain `ARGS="--json"` without `--fix` remains **`version: 1`**. Consumers (e.g. LUM-178) must use **`any_applied`** for mutation truth, not `dry_run` alone.
- **Safelist (slice 1):** `compose_up_service` (only for compose `exited` / `created` rows; service must be in **`S ∩ K`** at repair time), `ollama_pull_model` (models referenced from `.env` only; `ollama pull -- <model>` argv), `mkdir_backup_dir` (repo or fixed host roots with parent-dir must exist — see **`docs/decisions/065-lum-320-doctor-v2-shell-fix-remediation.md`**).
- **Safelist (LUM-342):** `compose_restart_service` — `docker compose restart <service>` for compose rows with **`State=running`** and **`Health=unhealthy`**; same **`K`** as slice 1; service must be in **`S ∩ K`** at repair time. Planner maps allowlist miss to **`skipped`**; service absent from **S** maps to **`error`** (anomalous when container was observed running). Operator must inspect logs before apply — restart does not fix disk/volume faults. See draft ADR `.cursor/adrs/LUM-342-doctor-compose-restart-unhealthy.md`.
- **Audit:** NDJSON append under `scripts/doctor/.audit/` (gitignored) or **`LUMOGIS_DOCTOR_AUDIT_DIR`**; long `.env` values (≥8 chars) redacted in audit fields. **Retention (LUM-338):** before each apply-path append, when active **`repair.ndjson`** size **≥ `LUMOGIS_DOCTOR_AUDIT_MAX_BYTES`** (default **5 MiB**), **`repair.sh`** rotates in-process to **`repair.ndjson.1` … `.{max_files-1}`** with **`LUMOGIS_DOCTOR_AUDIT_MAX_FILES`** generations (default **5**, minimum **2**); audit files **`0o600`**; rotation failure logs basename-only stderr and **still appends**. Optional OS **logrotate** is documented in **`scripts/doctor/README.md`** — do not run both built-in rotation and logrotate on the same path without coordination. **Concurrent apply (LUM-399):** when **`DOCTOR_APPLY_MUTATIONS=1`**, bash acquires **`repair.lock`** via **`flock -n`** (lockfile **`0600`**, audit dir created **`0700`** only if missing); a second concurrent apply on the same audit dir exits **4** with **`DOCTOR_REFUSED: another doctor --fix --apply is already running`**; dry-run skips the lock. **No `.env` rewriting** in slice 1.
- **Exit codes:** v1 **`0`–`3`** unchanged. **`4`** = `DOCTOR_REFUSED:` (e.g. `--fix --apply` in security posture, non-interactive apply without `--yes`, audit init failure on apply). **`5`–`9`** reserved for future v2 refusal/policy extensions. **`3`** still includes repair-stage contract failure (invalid / missing repair JSON contract before final `--json --fix` emit). **`--json --fix --apply`** refusal paths emit **empty stdout** (no partial v2 JSON).
- **Compose context:** `run.sh` snapshots `COMPOSE_FILE` / `COMPOSE_PROJECT_NAME` / `COMPOSE_PROFILES` at start and refreshes compose ps/config caches before the second detection pass after repairs.
- **`--apply`:** mutates only via the **safelist** above (`compose_up_service`, `compose_restart_service`, `mkdir_backup_dir`, `ollama_pull_model`).
- **`--yes`:** non-interactive confirmation for `--apply`. Without **`--yes`**, **`--fix --apply`** requires **both** stdin **and** stderr TTYs; otherwise **`DOCTOR_REFUSED:`** on stderr and **exit `4`**.
- **Out of slice 1:** editing `.env`, env-only apply toggles, and in-process `orchestrator.doctor --fix` (see LUM-322). **`docker compose restart` for unhealthy services** moved to **`compose_restart_service`** safelist (**LUM-342**, 2026-06).

Canonical slice-1 ADR: **`docs/decisions/065-lum-320-doctor-v2-shell-fix-remediation.md`**. Draft mirror (devtools): `.cursor/adrs/LUM-320-doctor-v2-fix-remediation.md`.
