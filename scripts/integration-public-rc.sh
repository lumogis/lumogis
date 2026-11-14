#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Deterministic integration subset (`integration and public_rc`) against the RC compose stack
# (docker-compose.yml + docker-compose.test.yml + docker-compose.public-rc-stack.yml).
# Used by Makefile targets `test-integration` and `verify-public-rc`.
# Also hosts the opt-in `ingest-live-*` lane (LUM-929, `make e2e-ingest-live`).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# LUM-618: the RC stack is the CONTAINED reference deployment. The egress overlay
# (isolated network + Squid egress proxy) is composed here and the community-egress
# profile is activated so the proxy actually starts — otherwise the mock (a
# community capability) would run uncontained while contained_capabilities.txt
# marks it contained. Pass C (compose-policy-check-egress) verifies the wiring.
export COMPOSE_PROFILES=community-egress
export COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml:docker-compose.egress.yml
# Absolute host path to the repo. Carried into stack-control (see docker-compose.public-rc-stack.yml)
# so its in-container `docker compose --force-recreate` resolves relative bind-mount sources to real
# host paths instead of non-existent /project/* dirs.
export HOST_PROJECT_DIR="$ROOT"
ENV_FILE="${INTEGRATION_ENV_FILE:-config/test.env.example}"

# Absolute path of $ENV_FILE (it may already be absolute, e.g. a tmp env in tests).
# Older callers below keep writing "$ROOT/$ENV_FILE"; new code uses this.
_rc_env_path() {
  case "$ENV_FILE" in
    /*) echo "$ENV_FILE" ;;
    *) echo "$ROOT/$ENV_FILE" ;;
  esac
}

_host_port_in_use() {
  local port=$1
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[.:]${port}$"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  return 1
}

_containers_on_host_port() {
  local want=$1
  local name hp
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    hp=$(docker inspect --format '{{range $p, $conf := .NetworkSettings.Ports}}{{range $conf}}{{.HostPort}} {{end}}{{end}}' "$name" 2>/dev/null || true)
    case " $hp " in
      *" $want "*) printf '%s\n' "$name" ;;
    esac
  done < <(docker ps --format '{{.Names}}')
}

_rc_compose_project() {
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  echo "${COMPOSE_PROJECT_NAME:-lumogis-test}"
}

_host_port_only_rc_stack() {
  local port=$1 rc_project="$(_rc_compose_project)"
  local c found=0
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    found=1
    case "$c" in
      "${rc_project}"-*) ;;
      *) return 1 ;;
    esac
  done < <(_containers_on_host_port "$port")
  [ "$found" -eq 1 ]
}

_free_host_port_for_rc() {
  local port=$1
  local c seen="" rc_project
  rc_project="$(_rc_compose_project)"
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    case "$c" in
      "${rc_project}"-*)
        # Port belongs to our RC stack (e.g. orchestrator recreate); do not stop it.
        continue
        ;;
    esac
    case " $seen " in *" $c "*) continue ;; esac
    seen="$seen $c"
    echo "[integration-public-rc] INFO: stopping $c to free port $port for RC stack" >&2
    docker stop "$c" >/dev/null 2>&1 || true
  done < <(_containers_on_host_port "$port")
}

_wait_port_free() {
  local port=$1
  local i=0
  while [ "$i" -lt 20 ]; do
    if ! _host_port_in_use "$port"; then
      return 0
    fi
    sleep 0.5
    i=$((i + 1))
  done
  return 1
}

_rc_qdrant_host_port() {
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  echo "${QDRANT_HOST_PORT:-6335}"
}

_ensure_host_dir_writable() {
  # Docker may have created a bind-mount target as root on first mount; host-side pytest
  # writes probe/inbox files there (LUM-400 for lumogis-data, LUM-929 for the inbox).
  local dir=$1
  mkdir -p "$dir"
  if [ -w "$dir" ]; then
    return 0
  fi
  if [ "$(id -u)" -eq 0 ]; then
    chmod 1777 "$dir"
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo chown "$(id -u)":"$(id -g)" "$dir" 2>/dev/null \
      || sudo chmod 1777 "$dir" 2>/dev/null \
      || true
  fi
}

_ensure_host_data_dir_writable() {
  _ensure_host_dir_writable "$ROOT/lumogis-data"
}

# Host ports the RC stack itself binds: Core and Qdrant. The RC stack publishes no
# 6333/6334, so a daily Qdrant on 6334 is none of its business (LUM-929: the old list
# stopped a Qdrant listening on 6334 on every gate-start).
_rc_preflight_ports() {
  echo "${ORCHESTRATOR_HOST_PORT:-8000} $(_rc_qdrant_host_port)"
}

_rc_preflight_host_ports() {
  local p
  for p in $(_rc_preflight_ports); do
    if ! _host_port_in_use "$p"; then
      continue
    fi
    if _host_port_only_rc_stack "$p"; then
      # RC stack already bound this port (e.g. gate-start with stack up).
      continue
    fi
    _free_host_port_for_rc "$p"
    if ! _wait_port_free "$p"; then
      echo "[integration-public-rc] ERROR: port $p still occupied after stop attempt. Free it manually and retry." >&2
      exit 1
    fi
  done
}

compose() {
  (cd "$ROOT" && docker compose --env-file "$ENV_FILE" "$@")
}

_recreate_orchestrator_rc() {
  compose up -d --no-deps --force-recreate orchestrator
  compose up -d --wait --no-deps orchestrator
}

# Pre-pull the embedding model into the (persistent) ollama_data volume. OLLAMA_SKIP_WAIT
# makes each orchestrator --force-recreate skip the on-boot model pull for a fast, deterministic
# restart; but the restart_e2e ingest proof needs embeddings to succeed. Pulling once here means
# every subsequent recreate finds the model already present. Best-effort: a failure only degrades
# the ingest assertions (which then fail loudly), it does not abort the gate.
_ensure_embedding_model_rc() {
  local model="${EMBEDDING_MODEL:-nomic-embed-text}"
  echo "[integration-public-rc] INFO: ensuring Ollama embedding model '${model}' is present (restart_e2e ingest needs embeddings)"
  compose exec -T ollama ollama pull "${model}" \
    || echo "[integration-public-rc] WARNING: could not pull '${model}'; restart_e2e ingest assertions may fail" >&2
}

# Hard requirement (LUM-929 lane): the boot-time pull of the extraction model in the
# orchestrator entrypoint only warns on failure and is skipped under OLLAMA_SKIP_WAIT.
# `ollama show` succeeds when the model is present; otherwise pull, and fail if that fails.
_require_ollama_model_rc() {
  local model=$1
  if compose exec -T ollama ollama show "$model" >/dev/null 2>&1; then
    return 0
  fi
  echo "[integration-public-rc] INFO: pulling required Ollama model '${model}'" >&2
  if ! compose exec -T ollama ollama pull "$model"; then
    echo "[integration-public-rc] ERROR: could not pull required Ollama model '${model}'" >&2
    exit 1
  fi
}

_wait_web_smoke_ready() {
  local origin="${LUMOGIS_WEB_BASE_URL:-http://127.0.0.1}"
  local email password i
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  email="${LUMOGIS_WEB_SMOKE_EMAIL:-${LUMOGIS_BOOTSTRAP_ADMIN_EMAIL:-}}"
  password="${LUMOGIS_WEB_SMOKE_PASSWORD:-${LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD:-}}"
  echo "[integration-public-rc] INFO: waiting for Caddy + auth at ${origin}" >&2
  for i in $(seq 1 60); do
    if curl -sf "${origin}/" 2>/dev/null | grep -q '<div id="root">'; then
      if [ -n "$email" ] && [ -n "$password" ]; then
        if E="$email" P="$password" curl -sf -o /dev/null -X POST "${origin}/api/v1/auth/login" \
          -H 'Content-Type: application/json' \
          -d "$(E="$email" P="$password" python3 -c 'import json,os; print(json.dumps({"email":os.environ["E"],"password":os.environ["P"]}))')"; then
          return 0
        fi
      elif curl -sf -o /dev/null "${origin}/health"; then
        return 0
      fi
    fi
    sleep 2
  done
  echo "[integration-public-rc] ERROR: stack not ready for Playwright at ${origin} after 120s" >&2
  return 1
}

cmd_up() {
  (cd "$ROOT" && test -f .env || cp config/test.env.example .env)
  # Create the default filesystem root on the host as the invoking user *before* compose up.
  # Otherwise the Docker daemon auto-creates the ./lumogis-data bind-mount target as root, and
  # the restart_e2e fallback test (which writes a probe file there from the host) hits EACCES.
  _ensure_host_data_dir_writable
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  _rc_preflight_host_ports
  compose up -d --wait
  if [[ "${COMPOSE_PROJECT_NAME:-}" == "lumogis-test" ]]; then
    bash "$ROOT/scripts/seed-public-rc-approvals-fixture.sh"
    bash "$ROOT/scripts/seed-public-rc-ingest-owner.sh"
    _ensure_embedding_model_rc
    _recreate_orchestrator_rc
    _wait_web_smoke_ready
  fi
}

_rc_pytest_preamble() {
  if [[ ! -d "$ROOT/.venv" ]]; then
    python3 -m venv "$ROOT/.venv"
  fi
  # shellcheck source=/dev/null
  source "$ROOT/.venv/bin/activate"
  pip install -q -r "$ROOT/orchestrator/requirements-dev.txt"
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$(_rc_env_path)")"
}

cmd_pytest() {
  _rc_pytest_preamble
  (
    cd "$ROOT/orchestrator"
    export LUMOGIS_WEB_BASE_URL=http://127.0.0.1
    export LUMOGIS_API_URL=http://127.0.0.1:8000
    pytest -c ../tests/integration/pytest.ini \
      ../tests/integration -v --tb=short -p no:cacheprovider \
      -m 'integration and public_rc'
  )
}

cmd_restart_e2e_pytest() {
  _rc_pytest_preamble
  export HOST_PROJECT_DIR="${HOST_PROJECT_DIR:-$ROOT}"
  _ensure_host_data_dir_writable
  if [[ "${COMPOSE_PROJECT_NAME:-}" == "lumogis-test" ]]; then
    bash "$ROOT/scripts/seed-public-rc-approvals-fixture.sh"
    bash "$ROOT/scripts/seed-public-rc-ingest-owner.sh"
    COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml \
      docker compose --env-file "$ENV_FILE" up -d --no-deps --force-recreate stack-control orchestrator
    sleep 5
  fi
  (
    cd "$ROOT/orchestrator"
    export LUMOGIS_WEB_BASE_URL=http://127.0.0.1
    export LUMOGIS_API_URL=http://127.0.0.1:8000
    pytest -c ../tests/integration/pytest.ini \
      ../tests/integration -v --tb=short -p no:cacheprovider \
      -m 'integration and restart_e2e'
  )
}

cmd_down() {
  compose down --remove-orphans
}

cmd_print_qdrant_host_port() {
  _rc_qdrant_host_port
}

# ---------------------------------------------------------------------------
# LUM-929 — opt-in live ingest-ownership lane (`make e2e-ingest-live`).
# Runs test_graph.py + test_integration_flow.py against the seeded RC stack with auth on and
# GRAPH_MODE=service, and fails on any skip. Not part of verify-public-rc.
# ---------------------------------------------------------------------------

# Read KEY from an env file the way rc_test_env_defaults.py parses it, ignoring the caller's
# environment (that script only emits keys unset in the environment). Never echo secrets.
_env_file_value() {
  local file=$1 key=$2
  env -u AUTH_SECRET -u COMPOSE_PROJECT_NAME python3 "$ROOT/scripts/rc_test_env_defaults.py" "$file" \
    | sed -n "s/^export ${key}=//p"
}

# Cheap refusals. Exit 2 and touch no Docker state (only a read-only `docker ps`).
_ingest_live_refusals() {
  local envp
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[integration-public-rc] ERROR: ingest-live needs python3 on PATH to read the RC env file" >&2
    exit 2
  fi
  envp="$(_rc_env_path)"
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$envp")"
  if [[ "${COMPOSE_PROJECT_NAME:-}" != "lumogis-test" ]]; then
    echo "[integration-public-rc] ERROR: ingest-live needs COMPOSE_PROJECT_NAME=lumogis-test (got '${COMPOSE_PROJECT_NAME:-}')" >&2
    exit 2
  fi
  if [[ "${AUTH_ENABLED:-}" != "true" ]]; then
    echo "[integration-public-rc] ERROR: ingest-live needs AUTH_ENABLED=true (got '${AUTH_ENABLED:-}')" >&2
    exit 2
  fi
  # Checkout guard: gate-start rewrites .env, and the RC lumogis-graph reads .env only.
  if [ -f "$ROOT/.env" ]; then
    local dot_proj dot_secret rc_secret
    dot_proj="$(_env_file_value "$ROOT/.env" COMPOSE_PROJECT_NAME)"
    dot_secret="$(_env_file_value "$ROOT/.env" AUTH_SECRET)"
    rc_secret="$(_env_file_value "$envp" AUTH_SECRET)"
    if [[ "$dot_proj" != "lumogis-test" || -z "$dot_secret" || "$dot_secret" != "$rc_secret" ]]; then
      echo "[integration-public-rc] ERROR: this checkout's .env is not the RC test template; run the lane from a worktree with no .env (gate-start copies config/test.env.example)" >&2
      exit 2
    fi
  fi
  # Concurrency guard: another checkout owning the shared lumogis-test project.
  if command -v docker >/dev/null 2>&1; then
    local owner
    while IFS= read -r owner; do
      [ -n "$owner" ] || continue
      if [[ "$owner" != "$ROOT" ]]; then
        echo "[integration-public-rc] ERROR: the lumogis-test compose project is owned by another checkout ($owner); its stack and fixed host ports would be torn down by gate-end" >&2
        exit 2
      fi
    done < <(docker ps -a --filter label=com.docker.compose.project=lumogis-test \
      --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u)
  fi
}

cmd_ingest_live_preflight() {
  _ingest_live_refusals
  echo "[integration-public-rc] INFO: ingest-live preflight ok" >&2
}

cmd_ingest_live_pytest() {
  _ingest_live_refusals
  _rc_pytest_preamble

  local cache_dir="$ROOT/.cache/lum929"
  local junit="${LUMOGIS_INGEST_LIVE_JUNIT:-$cache_dir/ingest-live-junit.xml}"
  local logs="$cache_dir/ingest-live-logs.txt"
  local record="$cache_dir/ingest-live-run-record.txt"
  local port="${ORCHESTRATOR_HOST_PORT:-8000}"
  local model="${LUMOGIS_INGEST_LIVE_LLM_MODEL:-llama3.2:3b}"
  mkdir -p "$cache_dir" "$(dirname "$junit")"
  rm -f "$logs"  # a passing run must not sit beside an older failure's logs

  local workspace="${WORKSPACE_HOST_DIR:-./ai-workspace}"
  case "$workspace" in /*) ;; *) workspace="$ROOT/$workspace" ;; esac
  _ensure_host_dir_writable "$workspace/inbox"

  _require_ollama_model_rc "$model"
  if ! compose exec -T ollama ollama run "$model" "Reply OK" >/dev/null; then
    echo "[integration-public-rc] ERROR: could not warm Ollama model '${model}'" >&2
    exit 1
  fi

  if ! compose exec -T lumogis-graph python -c "import jwt" >/dev/null 2>&1; then
    echo "[integration-public-rc] ERROR: lumogis-graph image cannot import PyJWT — every Bearer call to the KG would 401 (LUM-929 finding 10)" >&2
    exit 1
  fi

  local healthz
  healthz="$(curl -sf "http://127.0.0.1:${port}/healthz" || true)"
  if ! printf '%s' "$healthz" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("inbox_watcher") == "ok" else 1)' 2>/dev/null; then
    echo "[integration-public-rc] ERROR: inbox watcher not ok — seed INBOX_OWNER_USER_ID (gate-start)" >&2
    exit 1
  fi

  local pytest_ec guard_ec guard_out
  set +e
  (
    cd "$ROOT/orchestrator"
    export LUMOGIS_API_URL="http://127.0.0.1:${port}"
    export LUMOGIS_WEB_BASE_URL=http://127.0.0.1
    export LUMOGIS_INTEG_LLM_TIMEOUT_S="${LUMOGIS_INTEG_LLM_TIMEOUT_S:-300}"
    # -k, not --deselect: --deselect ids are relative to the rootdir that -c sets
    # (tests/integration) and a mismatch silently matches nothing. -k deselects, it does not skip.
    pytest -c ../tests/integration/pytest.ini \
      ../tests/integration/test_graph.py ../tests/integration/test_integration_flow.py \
      -m integration \
      -k "not test_signal_source_preview and not test_signal_source_add_idempotent" \
      -v --tb=short -p no:cacheprovider -rs --junitxml "$junit"
  )
  pytest_ec=$?
  # 10 (test_graph.py) + 15 (test_integration_flow.py) = 25 tests, 2 deselected => 23.
  # The floor equals the expected count: adding a test means raising it in the same commit.
  guard_out="$(python3 "$ROOT/scripts/junit_skip_guard.py" "$junit" --min-tests 23)"
  guard_ec=$?
  set -e
  printf '%s\n' "$guard_out"

  if [ "$pytest_ec" -ne 0 ] || [ "$guard_ec" -ne 0 ]; then
    {
      compose logs --no-color --tail 2000 orchestrator lumogis-graph 2>&1 || true
      echo "--- /healthz ---"
      curl -s "http://127.0.0.1:${port}/healthz" || true
    } >"$logs" 2>&1
    echo "[integration-public-rc] INFO: container logs saved to $logs" >&2
  fi

  local container_mode vol_created dirty
  container_mode="$(compose exec -T orchestrator printenv GRAPH_MODE 2>/dev/null | tr -d '\r' || true)"
  vol_created="$(docker volume inspect --format '{{.CreatedAt}}' "${COMPOSE_PROJECT_NAME}_postgres_data" 2>/dev/null || echo unknown)"
  dirty="$(cd "$ROOT" && git status --porcelain --untracked-files=no -- orchestrator services tests scripts 'docker-compose*.yml' config)"
  {
    echo "commit=$(cd "$ROOT" && git rev-parse HEAD)"
    if [ -n "$dirty" ]; then
      echo "dirty=yes"
      printf '%s\n' "$dirty"
    else
      echo "dirty=no"
    fi
    echo "postgres_volume_created_at=${vol_created}"
    echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "project=${COMPOSE_PROJECT_NAME}"
    echo "AUTH_ENABLED=${AUTH_ENABLED}"
    echo "GRAPH_MODE (orchestrator container)=${container_mode:-unknown}"
    echo "inbox_watcher=$(printf '%s' "$healthz" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("inbox_watcher"))' 2>/dev/null || echo unknown)"
    echo "llm_model=${model}"
    echo "deselected=test_signal_source_preview,test_signal_source_add_idempotent (real RSS egress to news.ycombinator.com; unrelated to LUM-884; maintainer decision 2026-11-11)"
    echo "pytest_exit=${pytest_ec}"
    echo "guard_exit=${guard_ec}"
    echo "guard:"
    printf '%s\n' "$guard_out"
  } | tee "$record"

  if [[ "$container_mode" != "service" ]]; then
    echo "[integration-public-rc] ERROR: orchestrator GRAPH_MODE is '${container_mode:-unknown}', not 'service'" >&2
    exit 1
  fi
  if [ "$pytest_ec" -ne 0 ]; then exit "$pytest_ec"; fi
  exit "$guard_ec"
}

cmd_print_preflight_ports() {
  _rc_preflight_ports
}

usage() {
  echo "usage: $0 up | pytest | restart-e2e-pytest | down | full-cycle | gate-start | gate-end | print-qdrant-host-port | print-preflight-ports | ingest-live-preflight | ingest-live-pytest" >&2
  exit 2
}

case "${1:-}" in
  print-qdrant-host-port)
    cmd_print_qdrant_host_port
    ;;
  print-preflight-ports)
    cmd_print_preflight_ports
    ;;
  ingest-live-preflight)
    cmd_ingest_live_preflight
    ;;
  ingest-live-pytest)
    cmd_ingest_live_pytest
    ;;
  up)
    cmd_up
    ;;
  pytest)
    cmd_pytest
    ;;
  down)
    cmd_down
    ;;
  full-cycle)
    cmd_up
    set +e
    cmd_pytest
    ec=$?
    set -e
    cmd_down || true
    exit "$ec"
    ;;
  gate-start)
    cmd_up
    ;;
  gate-pytest)
    cmd_pytest
    ;;
  gate-end)
    cmd_down
    ;;
  restart-e2e-pytest)
    cmd_restart_e2e_pytest
    ;;
  *)
    usage
    ;;
esac
