# Lumogis `make doctor` (LUM-199, LUM-320)

Host-side health checks for operators. Answers *“is my Lumogis install healthy?”* **without** requiring the orchestrator Python process to be up. **Read-only by default**; optional **`--fix`** (LUM-320) performs a **safelisted** repair pass (see ADR-061 amendment).

For automated tests, **`LUMOGIS_DOCTOR_REPO_ROOT`** may point at an isolated fixture directory (must contain **`docker-compose.yml`**); the default is the real checkout inferred from **`scripts/doctor/run.sh`**.

## Invocation

From the repository root (where `docker-compose.yml` lives):

```bash
make doctor
```

- **JSON output** (stable v1 contract for tooling such as LUM-178 / LUM-310):
  `make doctor ARGS="--json"`
  Portable alternative: `make doctor -- --json` (depends on your Make implementation).

- **Remediation / `--fix`** (LUM-320, **slice 1** — shell + `docker compose` + `repair.sh`; still no `orchestrator` imports):
  - **Dry-run (default):** `make doctor ARGS="--fix"` or `ARGS="--fix --dry-run"` — lists eligible repairs; no mutations. If both **`--apply`** and **`--dry-run`** appear, **`--dry-run` wins** (stderr warning).
  - **Apply:** `make doctor ARGS="--fix --apply --yes"` — **`--yes` is mandatory** in non-interactive contexts (CI, scripts, pipes). On an interactive TTY (stdin **and** stderr), you may omit **`--yes`** and confirm once when prompted.
  - **`--json --fix`** emits **`version: 2`** JSON (see `scripts/doctor/schema.v2.json`) including **`repairs[]`**, **`apply_requested`**, **`any_applied`**, and **`dry_run`** (slice 1: **`dry_run := !apply_requested`** — intent, not mutation truth; use **`any_applied`** for whether a mutating command succeeded). Plain **`--json`** without **`--fix`** stays **`version: 1`** (`schema.v1.json`).
  - **Does not** rewrite `.env` or other config files in slice 1.
  - **Audit trail (apply only):** NDJSON append under `scripts/doctor/.audit/` (gitignored) or set **`LUMOGIS_DOCTOR_AUDIT_DIR`** to an absolute directory (useful on read-only checkouts / export trees).

- **Security / audit category** (network + cold-cache cost; opt-in):
  `make doctor ARGS="--security"`
  or `LUMOGIS_DOCTOR_RUN_SECURITY=1 make doctor`

See also `docs/deployment/quickstart.md` and `docs/LUMOGIS_REFERENCE_MANUAL.md`.

## When the orchestrator is up: `make doctor` vs **`GET /admin/health`**

**`make doctor`** (this tree) is intentionally **host-side**: it does **not** import the orchestrator and is meant to answer *“is Compose / disk / published ports sane?”* **even when the Python app is down**.

When the orchestrator process **is** running, richer read-only probes (Postgres, Qdrant, capability registry, etc.) belong on the authenticated admin surface **`GET /admin/health`**, implemented in **`orchestrator/routes/admin.py`** — not a second parallel **`python -m orchestrator.doctor`** CLI. **LUM-322** defers that dedicated in-process doctor module until the revisit gates in **`docs/decisions/061-lum-199-lumogis-doctor.md`** fire.

Use your **normal admin credentials / bearer token** for **`/admin/health`** when **`AUTH_ENABLED=true`**. Docker healthchecks use **`/healthz`** (no JWT) by design — the app documents that healthchecks cannot send a Bearer while **`/admin/health`** requires auth when auth is on; do **not** disable auth or bypass admin gates as a “convenience” for health checks.

## CI parity / `make compose-test-doctor` (LUM-319)

For the same live-compose smoke CI uses (path-gated **`doctor-integration`** job in `.github/workflows/ci.yml`), run from a **disposable** checkout or back up `./.env` first — the target **overwrites** `./.env` with **`config/test.env.example`**, exports a **two-file** `COMPOSE_FILE` (no `docker-compose.public-rc-stack.yml`), brings up **`lumogis-test`**, runs **`make doctor ARGS="--json"`**, asserts minimal v1 JSON with **`jq`**, then **`docker compose down -v`**. Full spec: `.cursor/plans/LUM-319-doctor-ci-integration.plan.md`.

```bash
make compose-test-doctor
```

## Prerequisites

| Tool | When |
| --- | --- |
| `bash`, `docker`, `docker compose` | Always |
| `flock` | **Only** when using **`--fix --apply`** (util-linux; serialises concurrent apply on the same audit dir) |
| `jq` | **Only** when using `--json` |
| `python3` | Always (used internally for parsing; no `orchestrator` imports) |

`docker` can effectively grant root-equivalent access on the host. If you see permission errors, fix Docker socket permissions deliberately—do **not** casually add your user to the `docker` group on shared machines without understanding the risk.

## Environment

| Variable | Purpose |
| --- | --- |
| `COMPOSE_FILE` | Passed through to `docker compose` (e.g. `docker-compose.yml:docker-compose.ghcr.yml`). |
| `COMPOSE_PROJECT_NAME` | Compose project name (`lumogis-test`, etc.). |
| `GRAPH_MODE` | When `service`, doctor may warn if FalkorDB overlay is likely missing from `COMPOSE_FILE`. |
| `ORCHESTRATOR_HOST_PORT` | Override published orchestrator port when auto-detection from `docker compose config` is wrong. |
| `LUMOGIS_DEFAULT_LLM`, `EMBEDDING_MODEL` | Optional model presence checks via `ollama list` (see plan). |
| `BACKUP_DIR` | Optional storage check; when set but missing, **`--fix`** may offer `mkdir_backup_dir` (see ADR-061). |
| `LUMOGIS_DOCTOR_AUDIT_DIR` | Override directory for **`--fix --apply`** NDJSON audit (default: `scripts/doctor/.audit/` under the repo). |
| `LUMOGIS_DOCTOR_AUDIT_MAX_BYTES` | Rotate active audit log when **`repair.ndjson`** size is **≥** this before the next append (default **`5242880`** = 5 MiB). |
| `LUMOGIS_DOCTOR_AUDIT_MAX_FILES` | Max files in the rotation chain including active **`repair.ndjson`** (default **`5`**; minimum **`2`**). |
| `LUMOGIS_DOCTOR_REPAIR_TIMEOUT_COMPOSE_UP` | Seconds for `docker compose up` repair (default `120`). |
| `LUMOGIS_DOCTOR_REPAIR_TIMEOUT_OLLAMA_PULL` | Seconds for `ollama pull` repair (default `1800`). |
| `LUMOGIS_DOCTOR_REPAIR_TIMEOUT_MKDIR` | Seconds for `mkdir` repair (default `30`). |
| `LUMOGIS_DOCTOR_RUN_SECURITY` | Set to `1` to enable the security category (same as `--security`). |
| `LUMOGIS_DOCTOR_SECURITY_STRICT` | When security is enabled, set to `1` to treat audit/bandit warnings as errors. |

At startup, `run.sh` snapshots **`COMPOSE_FILE`**, **`COMPOSE_PROJECT_NAME`**, and **`COMPOSE_PROFILES`** into **`LUMOGIS_DOCTOR_COMPOSE_*`** and re-exports them so checks and repairs target the same compose project identity for the whole run.

## Audit log retention (`--fix --apply`)

Each applied repair appends one NDJSON line to **`{LUMOGIS_DOCTOR_AUDIT_DIR}/repair.ndjson`** (default **`scripts/doctor/.audit/repair.ndjson`**, gitignored). Before append, when the active file is **≥ `LUMOGIS_DOCTOR_AUDIT_MAX_BYTES`**, **`repair.sh`** rotates in-process:

| File | Role |
| --- | --- |
| `repair.ndjson` | Active log (append target) |
| `repair.ndjson.1` | Newest backup |
| `repair.ndjson.2` … `.N` | Older generations (`N = LUMOGIS_DOCTOR_AUDIT_MAX_FILES - 1`) |

Defaults: **5 MiB** active cap, **5** files total. Rotation is rename-only (no content rewrite). New and rotated files are mode **`0600`**; the audit directory stays **`0700`**. A single NDJSON line larger than the byte cap may leave the active file oversized until the **next** append triggers rotation.

Use **either** built-in rotation (default) **or** external logrotate on the same paths — not both without coordination (e.g. logrotate `copytruncate` fights in-process renames).

Optional logrotate example (operator host; not shipped by Lumogis):

```text
/path/to/lumogis/scripts/doctor/.audit/repair.ndjson {
    size 5M
    rotate 4
    missingok
    notifempty
    compress
}
```

Set **`LUMOGIS_DOCTOR_AUDIT_DIR`** when the repo checkout is read-only but you still want apply audit on a writable path.

Concurrent **`--fix --apply`** on the same **`LUMOGIS_DOCTOR_AUDIT_DIR`** is serialised with a non-blocking **`repair.lock`** (`flock -n`); a second run exits **`4`** with **`DOCTOR_REFUSED:`** on stderr.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | No errors and no warnings in the summary (ok and/or skipped only). |
| `1` | At least one **warn**, zero **error**. |
| `2` | At least one **error**. |
| `3` | Fatal: missing `docker` / `docker compose`, missing `jq` with `--json`, not a Lumogis checkout (no top-level `docker-compose.yml`), formatter failure, or repair contract failure. Stderr lines prefixed with `DOCTOR_FATAL:`; **no JSON on stdout** in this case. |
| `4` | Refused: **`--fix --apply`** without **`--yes`** when stdin/stderr are not both TTYs; **`--fix --apply`** while **`--security`** is enabled; audit directory cannot be initialised before apply; or another **`--fix --apply`** holds **`repair.lock`** in the same **`LUMOGIS_DOCTOR_AUDIT_DIR`**. Stderr lines prefixed with `DOCTOR_REFUSED:`; with **`--json --fix --apply`** refusal, **stdout is empty** (no partial JSON).

## JSON schema

Machine contracts:

- **`scripts/doctor/schema.v1.json`** — **`--json`** without **`--fix`**: top-level `version: 1`.
- **`scripts/doctor/schema.v2.json`** — **`--json --fix`**: top-level `version: 2`, **`apply_requested`**, **`any_applied`**, **`dry_run`**, **`repairs[]`**.

Consumers **MUST** ignore unknown keys at the same major `version` where applicable.

## Security category behaviour

Default runs **do not** invoke `make audit-local`, `bandit`, or create `.venv-audit` / `.venv-bandit-check`. When `--security` / `LUMOGIS_DOCTOR_RUN_SECURITY=1` is used, expect **npm/pip advisory network** traffic and long cold-cache runs (minutes on first use). A single **`skipped`** row is emitted for security when the category is disabled.

## `COMPOSE_FILE` and FalkorDB

If `GRAPH_MODE=service` but `COMPOSE_FILE` does not include `docker-compose.falkordb.yml` while that file exists in the repo, doctor emits a **warn** that the stack may not match `GRAPH_MODE`.
