#!/bin/bash
# Lumogis orchestrator entrypoint.
# Do NOT use set -e — pull failures must be non-fatal (degraded-mode startup).

# LibreChat bind-mount target — generated at runtime; gitignored in the repo.
# Seed from the tracked cold-start template so fresh clones and single-file mounts work.
_LIBRECHAT_CFG="/project/config/librechat.yaml"
_LIBRECHAT_TEMPLATE="/project/config/librechat.coldstart.yaml"
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
