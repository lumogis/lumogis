#!/bin/bash
# Lumogis orchestrator entrypoint.
# Do NOT use set -e — pull failures must be non-fatal (degraded-mode startup).

# LibreChat bind-mount target — generated at runtime; gitignored in the repo.
# Seed from the tracked cold-start template so fresh clones and single-file mounts work.
_LIBRECHAT_CFG="/project/config/librechat.yaml"
_LIBRECHAT_TEMPLATE="/project/config/librechat.coldstart.yaml"
# Docker can create `librechat.yaml` as an empty directory when the bind-mount
# source path was missing on first boot; LibreChat then logs EISDIR / invalid YAML.
if [ -d "$_LIBRECHAT_CFG" ]; then
    rm -rf "$_LIBRECHAT_CFG"
    echo "[entrypoint] Removed directory at ${_LIBRECHAT_CFG}; expected a regular file — re-seeding below"
fi
if [ ! -f "$_LIBRECHAT_CFG" ] || [ ! -s "$_LIBRECHAT_CFG" ]; then
    if [ -f "$_LIBRECHAT_TEMPLATE" ]; then
        cp "$_LIBRECHAT_TEMPLATE" "$_LIBRECHAT_CFG"
        echo "[entrypoint] Seeded librechat.yaml from librechat.coldstart.yaml"
    else
        echo "[entrypoint] WARNING: missing $_LIBRECHAT_TEMPLATE — LibreChat may fail until config exists" >&2
    fi
fi

OLLAMA_URL="${OLLAMA_URL:-http://ollama:11434}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-nomic-embed-text}"
# Comma-separated chat LLM(s) to pull after the embedder (default: small Llama 3.2).
# Use OLLAMA_EXTRA_MODELS= to disable (empty).
OLLAMA_EXTRA_MODELS="${OLLAMA_EXTRA_MODELS-llama3.2:3b}"
HOST_ENV="/project/.env"

# ── Auto-generate secrets ────────────────────────────────────────────────────
# On first run, .env has placeholder values from .env.example.
# Generate real secrets and write them to the bind-mounted .env so they
# persist and take effect for ALL services on the next docker compose up -d.
# The current run uses the original placeholders (consistent across services).
if [ -f "$HOST_ENV" ]; then
    _gen() { python3 -c "import secrets; print(secrets.token_hex(32))"; }
    _changed=0
    for pair in \
        "JWT_SECRET=change-me-in-production" \
        "JWT_REFRESH_SECRET=change-me-in-production" \
        "RESTART_SECRET=change-me-to-a-random-secret"; do
        key="${pair%%=*}"
        if grep -q "^${pair}$" "$HOST_ENV"; then
            sed -i "s|^${pair}$|${key}=$(_gen)|" "$HOST_ENV"
            echo "[entrypoint] Generated ${key}"
            _changed=1
        fi
    done
    [ "$_changed" -eq 1 ] && echo "[entrypoint] Secrets saved to .env — active on next restart."
fi

# Warn if /data is not mounted (misconfigured FILESYSTEM_ROOT)
if [ ! -d "/data" ]; then
    echo "[entrypoint] WARNING: /data is not mounted. Check FILESYSTEM_ROOT in .env." >&2
fi

# ── AUTH_SECRET hardening (post-/verify-plan follow-up) ─────────────────────
# Refuse to boot when AUTH_ENABLED=true but AUTH_SECRET is unset or a known
# placeholder. The entrypoint auto-rotates JWT_SECRET / JWT_REFRESH_SECRET /
# RESTART_SECRET above, but intentionally does NOT auto-rotate AUTH_SECRET —
# operators flip family-LAN mode on deliberately and must own that secret.
# A duplicate Python-side guard lives in main._enforce_auth_consistency for
# the case where the entrypoint is bypassed (e.g. local dev `uvicorn main:app`).
_AUTH_ENABLED_NORM="$(echo "${AUTH_ENABLED:-false}" | tr '[:upper:]' '[:lower:]' | xargs)"
if [ "$_AUTH_ENABLED_NORM" = "true" ]; then
    _AUTH_SECRET_NORM="$(echo "${AUTH_SECRET:-}" | xargs)"
    case "$_AUTH_SECRET_NORM" in
        ""|"change-me-in-production"|"__GENERATE_ME__")
            echo "[entrypoint] FATAL: AUTH_ENABLED=true but AUTH_SECRET is unset or a placeholder." >&2
            echo "[entrypoint]        Generate a real secret with: openssl rand -hex 32" >&2
            echo "[entrypoint]        Then set AUTH_SECRET in .env and restart." >&2
            echo "[entrypoint]        AUTH_SECRET is intentionally NOT auto-rotated — see" >&2
            echo "[entrypoint]        .cursor/plans/family_lan_multi_user.plan.md follow-ups." >&2
            exit 1
            ;;
    esac
fi

# ── LUMOGIS_CREDENTIAL_KEY[S] hardening ─────────────────────────────────────
# Refuse to boot when AUTH_ENABLED=true but no usable Fernet key is configured
# for the per-user connector credential subsystem (services/connector_credentials.py).
# Without a key, every credential PUT/GET/resolve would raise at request time
# and the container would otherwise come up "healthy". A duplicate Python-side
# guard lives in main._enforce_auth_consistency for the case where the
# entrypoint is bypassed (e.g. local dev `uvicorn main:app`).
#
# Honour LUMOGIS_CREDENTIAL_KEYS (CSV, newest first) over LUMOGIS_CREDENTIAL_KEY
# when set — same precedence as services.connector_credentials._load_keys().
#
# Critical: NOT auto-rotated by the loop above. Auto-generating this key would
# silently destroy every existing encrypted credential on the next boot if
# .env is ever re-bootstrapped. Operators must own this key.
if [ "$_AUTH_ENABLED_NORM" = "true" ]; then
    _CRED_KEYS_NORM="$(echo "${LUMOGIS_CREDENTIAL_KEYS:-${LUMOGIS_CREDENTIAL_KEY:-}}" | xargs)"
    case "$_CRED_KEYS_NORM" in
        ""|"change-me-in-production"|"__GENERATE_ME__")
            echo "[entrypoint] FATAL: AUTH_ENABLED=true but LUMOGIS_CREDENTIAL_KEY[S] is unset or a placeholder." >&2
            echo "[entrypoint]        Generate with: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"" >&2
            echo "[entrypoint]        Then set LUMOGIS_CREDENTIAL_KEY in .env (or LUMOGIS_CREDENTIAL_KEYS for a CSV during rotation) and restart." >&2
            echo "[entrypoint]        Key is intentionally NOT auto-rotated — losing it makes every per-user connector credential unrecoverable." >&2
            exit 1
            ;;
    esac
fi

# ── Apply Postgres migrations ────────────────────────────────────────────────
# postgres/init.sql only runs on first init of an empty data volume; subsequent
# schema changes live in postgres/migrations/*.sql and must be applied manually
# unless something runs them. We do that here so existing installs heal on boot.
# The runner is idempotent and tracks applied files in a schema_migrations table.
if [ -f "/app/db_migrations.py" ]; then
    echo "[entrypoint] Running Postgres migrations..."
    if ! python3 /app/db_migrations.py; then
        echo "[entrypoint] WARNING: migration runner exited non-zero — see logs above." >&2
        echo "[entrypoint] Continuing startup; orchestrator may run in degraded mode." >&2
    fi
fi

# ── Legacy `user_id='default'` remap (post-013 scope model) ──────────────────
# The 013-memory-scopes.sql migration introduces a first-class `scope` column
# whose `personal` arm gates visibility on `user_id`. Any leftover rows from
# the pre-multi-user dev period that still carry `user_id='default'` would be
# stranded under nobody's account once `AUTH_ENABLED=true` is flipped on.
# The remap script is idempotent (subsequent boots are no-ops) and is the only
# mutating operation that touches existing data — kept separate from the
# migration runner so the SQL file stays pure-SQL and operationally debuggable.
if [ -f "/app/db_default_user_remap.py" ]; then
    echo "[entrypoint] Running legacy default-user remap..."
    if ! python3 /app/db_default_user_remap.py; then
        echo "[entrypoint] WARNING: default-user remap exited non-zero — see WARN line above." >&2
        echo "[entrypoint] Continuing startup; legacy 'default'-user rows remain in place." >&2
    fi
fi

echo "[entrypoint] Waiting for Ollama at $OLLAMA_URL ..."
for i in $(seq 1 60); do
    if curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        echo "[entrypoint] Ollama ready."
        break
    fi
    echo "[entrypoint] Waiting... ($i/60)"
    sleep 2
done

# Check if embedding model is already present using /api/show (returns 200 if present, 404 if not).
# More reliable than grepping /api/tags which includes :tag suffixes.
if curl -sf -X POST "$OLLAMA_URL/api/show" \
       -H "Content-Type: application/json" \
       -d "{\"name\": \"$EMBEDDING_MODEL\"}" > /dev/null 2>&1; then
    echo "[entrypoint] $EMBEDDING_MODEL already present."
else
    echo "[entrypoint] Pulling $EMBEDDING_MODEL (~300 MB, may take several minutes)..."
    if curl -sf -X POST "$OLLAMA_URL/api/pull" \
           -H "Content-Type: application/json" \
           -d "{\"name\": \"$EMBEDDING_MODEL\", \"stream\": false}" \
           > /dev/null 2>&1; then
        echo "[entrypoint] $EMBEDDING_MODEL pulled successfully."
    else
        echo "[entrypoint] WARNING: Failed to pull $EMBEDDING_MODEL." >&2
        echo "[entrypoint] Search and ingest will be unavailable until the model is pulled." >&2
        echo "[entrypoint] Use the dashboard (Settings → Models) to pull it manually." >&2
        # Do NOT exit — let the orchestrator start in degraded mode.
    fi
fi

# Optional chat LLM(s) pulled after the embedder (see OLLAMA_EXTRA_MODELS).
_ensure_ollama_model() {
    _name="$1"
    [ -z "$_name" ] && return 0
    if curl -sf -X POST "$OLLAMA_URL/api/show" \
           -H "Content-Type: application/json" \
           -d "{\"name\": \"$_name\"}" > /dev/null 2>&1; then
        echo "[entrypoint] $_name already present."
        return 0
    fi
    echo "[entrypoint] Pulling $_name (may take several minutes on first start)..."
    if curl -sf -X POST "$OLLAMA_URL/api/pull" \
           -H "Content-Type: application/json" \
           -d "{\"name\": \"$_name\", \"stream\": false}" \
           > /dev/null 2>&1; then
        echo "[entrypoint] $_name pulled successfully."
    else
        echo "[entrypoint] WARNING: Failed to pull $_name." >&2
        echo "[entrypoint] Pull it from the dashboard (Settings → Models) or: docker compose exec ollama ollama pull $_name" >&2
    fi
}

if [ -n "$OLLAMA_EXTRA_MODELS" ]; then
    _old_ifs="$IFS"
    IFS=','
    for _raw in $OLLAMA_EXTRA_MODELS; do
        _m="$(echo "$_raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -n "$_m" ] && _ensure_ollama_model "$_m"
    done
    IFS="$_old_ifs"
fi

exec "$@"
