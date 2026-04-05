#!/bin/bash
# Lumogis orchestrator entrypoint.
# Do NOT use set -e — pull failures must be non-fatal (degraded-mode startup).

OLLAMA_URL="${OLLAMA_URL:-http://ollama:11434}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-nomic-embed-text}"
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

exec "$@"
