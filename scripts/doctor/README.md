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
  - **Shortcut targets (LUM-343):** `make doctor-fix` / `make doctor-fix-dry` (dry-run) and `make doctor-fix-apply` (apply). These only prepend the `--fix` flags and still pass **`ARGS`** through, e.g. `make doctor-fix ARGS="--json"` or `make doctor-fix-apply ARGS="--yes"`. They are pure ergonomics — identical behaviour and JSON contract to the equivalent `make doctor ARGS=...`.
  - **`--json --fix`** emits **`version: 2`** JSON (see `scripts/doctor/schema.v2.json`) including **`repairs[]`**, **`apply_requested`**, **`any_applied`**, and **`dry_run`** (slice 1: **`dry_run := !apply_requested`** — intent, not mutation truth; use **`any_applied`** for whether a mutating command succeeded). Plain **`--json`** without **`--fix`** stays **`version: 1`** (`schema.v1.json`).
  - **`.env` edits (slice 2, LUM-341):** doctor may **append** a missing, non-secret, safelisted key (e.g. `GRAPH_MODE`) — **append-only**, never modifying/deleting existing lines, never a secret-shaped key. **Off by default**; requires `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1` **in addition to** `--fix --apply`. See **`.env` config-edit safelist** below and ADR-065 § Amendment — slice 2.
  - **Repair kinds (safelist):** `compose_up_service` (exited/created only), `compose_restart_service` (running + unhealthy only; **LUM-342**), `ollama_pull_model`, `mkdir_backup_dir`, `set_env_key` (append-only, opt-in; **LUM-341**). See **Safelist details** below.
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
| `jq` | **Only** when using `--json` (shipped in the orchestrator image so in-container `make compose-test` doctor JSON tests pass — LUM-337) |
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
| `LUMOGIS_DOCTOR_REPAIR_TIMEOUT_COMPOSE_RESTART` | Seconds for `docker compose restart` repair (default `120`). |
| `LUMOGIS_DOCTOR_REPAIR_TIMEOUT_OLLAMA_PULL` | Seconds for `ollama pull` repair (default `1800`). |
| `LUMOGIS_DOCTOR_REPAIR_TIMEOUT_MKDIR` | Seconds for `mkdir` repair (default `30`). |
| `LUMOGIS_DOCTOR_RESTART_LOOP_MAX` | **LUM-494** restart-loop guard: max **applied** `compose_restart_service` repairs for the **same** service counted in the audit log before a further restart is refused (default `3`; set `0` to disable the guard). |
| `LUMOGIS_DOCTOR_RESTART_LOOP_WINDOW_SEC` | Lookback window (seconds) for the restart-loop guard count (default `3600`; `0` = count all audit history). |
| `LUMOGIS_DOCTOR_CORE_SERVICES_FILE` | **LUM-340** override path to the core-service allowlist (K) manifest. Default: `core-services.json` next to `repair.sh`. Invalid/missing files fall back to the next candidate, then to the built-in set. |
| `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS` | **LUM-341** set to `1` to enable append-only `.env` edits (`set_env_key`). Off by default; required **in addition to** `--fix --apply`. |
| `LUMOGIS_DOCTOR_ENV_SAFELIST_FILE` | **LUM-341** override path to the editable-key manifest. Default: `env-safelist.json` next to `repair.sh`. Invalid/missing files fall back to the built-in set; never widens it. |
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

## Restart-loop guard (`compose_restart_service`, LUM-494)

Blind `docker compose restart` does **not** fix disk-full, volume corruption, or crash-recovery faults, and repeatedly restarting a stateful DB (Postgres, Qdrant, FalkorDB) can mask the real fault or deepen it. To prevent doctor from driving such a loop, every `compose_restart_service` repair is checked against the **applied** restart history for the *same* service in **`repair.ndjson`**:

- The guard counts rows where `kind = compose_restart_service` **and** `outcome = applied` **and** `target.service` matches, with `ts` inside the lookback window.
- When that count reaches **`LUMOGIS_DOCTOR_RESTART_LOOP_MAX`** (default **`3`**) within **`LUMOGIS_DOCTOR_RESTART_LOOP_WINDOW_SEC`** (default **`3600`**), the repair is emitted as **`skipped`** with a `restart-loop guard:` message instead of being executed — in both **`--fix`** (dry-run, so operators see it coming) and **`--fix --apply`**.
- Skipped repairs are **not** audited, so a refused restart does not itself advance the counter.
- Set **`LUMOGIS_DOCTOR_RESTART_LOOP_MAX=0`** to disable the guard, or **`LUMOGIS_DOCTOR_RESTART_LOOP_WINDOW_SEC=0`** to count the full audit history regardless of age.

When the guard trips, inspect `docker compose logs <service>`, disk usage, and volume health before retrying; clearing the recent restart rows (or waiting out the window) re-arms a single restart.

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

## Safelist details (`--fix`)

| Kind | When emitted | Command | Notes |
| --- | --- | --- | --- |
| `compose_up_service` | Compose `exited` / `created` | `docker compose up -d <service>` | Service must be in **`S ∩ K`**. |
| `compose_restart_service` | Compose `running` + `unhealthy` | `docker compose restart <service>` | **LUM-342.** Same **`K`** as `compose_up_service`. Inspect logs before **`--apply`** — restart does **not** fix disk-full, volume corruption, or crash-recovery faults; may cause restart loops on stateful DBs (Postgres, Qdrant, FalkorDB). Guarded by the **restart-loop guard** (see below). |
| `ollama_pull_model` | Missing models referenced in `.env` | `docker compose exec … ollama pull -- <model>` | Model must match `.env` inference targets. |
| `mkdir_backup_dir` | `BACKUP_DIR` set but path missing | `mkdir <path>` | Repo or fixed host roots only; parent must exist. |
| `set_env_key` | Safelisted key absent from `.env` (opt-in) | append `KEY=value` to `.env` | **LUM-341.** Append-only; non-secret; off unless `LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1`. See below. |

### `.env` config-edit safelist (LUM-341)

Slice 2 lets doctor **append a missing, non-secret key** to `${LUMOGIS_REPO_ROOT}/.env`. It is deliberately conservative:

- **Append-only.** Only adds a key that is **absent** (including commented-out forms). It never modifies, reorders, or deletes existing lines — so operator comments and secrets are never at risk.
- **Opt-in twice.** Off unless **`LUMOGIS_DOCTOR_ALLOW_ENV_EDITS=1`** is set **and** you pass `--fix --apply` (`--yes` rules unchanged). Detection rows are only surfaced when the opt-in is set.
- **Versioned manifest.** Editable keys + defaults live in **`scripts/doctor/env-safelist.json`** (override path: **`LUMOGIS_DOCTOR_ENV_SAFELIST_FILE`**). A malformed/missing manifest falls back to a built-in set; it can never widen the safelist. The value written is the **manifest default**, not anything from the check stream.
- **Hard secret denylist.** Any key whose name matches `*_PASSWORD`/`*_SECRET`/`*_KEY`/`*_TOKEN`/`*_DSN`/`*_CREDENTIALS` or `DEK*`/`JWT*` is refused regardless of the manifest. Doctor never generates secrets.
- **Backup + rollback.** Before appending, `.env` is copied to **`.env.bak-<UTC-ts>`** (`0600`); the write is atomic (temp file + rename). The backup path is reported in the repair row. To undo: restore the backup, or delete the trailing `# added by lumogis doctor (LUM-341)` lines.
- **Scope.** Only the canonical `.env`; `.env.local`/overrides are out of scope. `.env` stays gitignored — doctor never writes to a tracked file.

Full threat model: `docs/decisions/065-lum-320-doctor-v2-shell-fix-remediation.md` § Amendment — slice 2.

### Core-service allowlist (K) — versioned manifest (LUM-340)

Repairs that start or restart a container (`compose_up_service`,
`compose_restart_service`) only target services in **`S ∩ K`**, where **S** is
the live `docker compose config` and **K** is the *core-service allowlist* —
deliberately narrower than S so doctor never mutates an arbitrary service.

K is defined by the versioned manifest **`scripts/doctor/core-services.json`**:

```json
{ "version": 1, "services": ["orchestrator", "postgres", "caddy", "..."] }
```

- `repair.sh` loads the manifest from (in order) **`LUMOGIS_DOCTOR_CORE_SERVICES_FILE`**, then `core-services.json` beside the script, then `<repo>/scripts/doctor/core-services.json`, and finally a **built-in copy** of the set if none parse.
- Only entries matching the service-name pattern `^[a-z0-9][a-z0-9._-]{0,62}$` are accepted; a malformed or empty manifest can never *widen* K beyond the built-in fallback — it falls through instead.
- To add or remove a core service, edit the manifest (bump `version` on a breaking change) **and** the built-in fallback in `repair.sh`; a test asserts the two stay in sync.

See `docs/decisions/061-lum-199-lumogis-doctor.md`.

Example (dry-run restart for unhealthy Postgres):

```bash
make doctor ARGS="--json --fix"
# repairs[] may include kind=compose_restart_service when ps shows health=unhealthy

make doctor ARGS="--fix --apply --yes"   # mutates only with explicit --yes
```
