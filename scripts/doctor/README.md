# Lumogis `make doctor` (LUM-199)

Read-only host-side health checks for operators. Answers *“is my Lumogis install healthy?”* **without** requiring the orchestrator Python process to be up.

For automated tests, **`LUMOGIS_DOCTOR_REPO_ROOT`** may point at an isolated fixture directory (must contain **`docker-compose.yml`**); the default is the real checkout inferred from **`scripts/doctor/run.sh`**.

## Invocation

From the repository root (where `docker-compose.yml` lives):

```bash
make doctor
```

- **JSON output** (stable v1 contract for tooling such as LUM-178 / LUM-310):
  `make doctor ARGS="--json"`
  Portable alternative: `make doctor -- --json` (depends on your Make implementation).

- **Security / audit category** (network + cold-cache cost; opt-in):
  `make doctor ARGS="--security"`
  or `LUMOGIS_DOCTOR_RUN_SECURITY=1 make doctor`

See also `docs/deployment/quickstart.md` and `docs/LUMOGIS_REFERENCE_MANUAL.md`.

## CI parity / `make compose-test-doctor` (LUM-319)

For the same live-compose smoke CI uses (path-gated **`doctor-integration`** job in `.github/workflows/ci.yml`), run from a **disposable** checkout or back up `./.env` first — the target **overwrites** `./.env` with **`config/test.env.example`**, exports a **two-file** `COMPOSE_FILE` (no `docker-compose.public-rc-stack.yml`), brings up **`lumogis-test`**, runs **`make doctor ARGS="--json"`**, asserts minimal v1 JSON with **`jq`**, then **`docker compose down -v`**. Full spec: `.cursor/plans/LUM-319-doctor-ci-integration.plan.md`.

```bash
make compose-test-doctor
```

## Prerequisites

| Tool | When |
| --- | --- |
| `bash`, `docker`, `docker compose` | Always |
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
| `BACKUP_DIR` | Optional storage check (directory mtime). |
| `LUMOGIS_DOCTOR_RUN_SECURITY` | Set to `1` to enable the security category (same as `--security`). |
| `LUMOGIS_DOCTOR_SECURITY_STRICT` | When security is enabled, set to `1` to treat audit/bandit warnings as errors. |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | No errors and no warnings in the summary (ok and/or skipped only). |
| `1` | At least one **warn**, zero **error**. |
| `2` | At least one **error**. |
| `3` | Fatal: missing `docker` / `docker compose`, missing `jq` with `--json`, not a Lumogis checkout (no top-level `docker-compose.yml`), or formatter failure. Stderr lines prefixed with `DOCTOR_FATAL:`; **no JSON on stdout** in this case. |

## JSON schema

Machine contract: `scripts/doctor/schema.v1.json` — top-level `version: 1`, closed `category` enum, `generated_at` in UTC (`…Z`). Consumers **MUST** ignore unknown keys at the same major `version`.

## Security category behaviour

Default runs **do not** invoke `make audit-local`, `bandit`, or create `.venv-audit` / `.venv-bandit-check`. When `--security` / `LUMOGIS_DOCTOR_RUN_SECURITY=1` is used, expect **npm/pip advisory network** traffic and long cold-cache runs (minutes on first use). A single **`skipped`** row is emitted for security when the category is disabled.

## `COMPOSE_FILE` and FalkorDB

If `GRAPH_MODE=service` but `COMPOSE_FILE` does not include `docker-compose.falkordb.yml` while that file exists in the repo, doctor emits a **warn** that the stack may not match `GRAPH_MODE`.
