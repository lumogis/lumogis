# First-run quickstart

Self-contained path for a new self-hoster: clone, configure, start with Docker Compose, pull default models, and confirm the stack is healthy.

---

## Prerequisites

- Docker and Docker Compose installed (Docker Engine + Compose v2, or Docker Desktop)
- 8 GB RAM minimum (default Ollama embedder + chat model)
- Git

---

## Quick start (5 steps)

1. **Clone the repo**

   ```bash
   git clone https://github.com/lumogis/lumogis
   ```

2. **Enter the directory**

   ```bash
   cd lumogis
   ```

3. **Create `.env`**

   ```bash
   cp .env.example .env
   ```

   No edits are required for a default LAN trial (`AUTH_ENABLED` stays off). To use **pre-built images** from GHCR instead of a local build, set **`COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`** in `.env` (see comments at the top of `.env.example`).

4. **Start the stack**

   ```bash
   docker compose up -d
   ```

   First boot can take several minutes (image build or pull, Postgres init, Ollama model downloads).

5. **Open the UI**

   Open **http://localhost/** (Caddy → Lumogis Web) and complete first-run setup in the browser.

   Orchestrator API/Swagger is also available at **http://localhost:8000/docs** if you need it.

---

## Pull the default Ollama model

On first start, the orchestrator entrypoint (`orchestrator/docker-entrypoint.sh`) waits for Ollama and pulls:

- **`nomic-embed-text`** — embedding model (`EMBEDDING_MODEL` in `.env.example`)
- **`llama3.2:3b`** — default chat model (`OLLAMA_EXTRA_MODELS` in `.env.example`)

Watch progress:

```bash
docker compose logs -f orchestrator
```

If a pull fails (entrypoint logs a WARNING and continues in degraded mode), pull manually:

```bash
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
```

Or use **Settings → Models** in the web UI.

---

## Verify Postgres migrations ran

Fresh Postgres volumes are bootstrapped from **`postgres/init.sql`**. Further schema changes are applied **automatically** on orchestrator startup by **`python3 /app/db_migrations.py`** (idempotent; tracks applied files in `schema_migrations`).

Confirm in logs:

```bash
docker compose logs orchestrator | grep -i migration
```

You should see `[entrypoint] Running Postgres migrations...` without a fatal error. If the migration runner exits non-zero, the entrypoint logs a WARNING and the service may be degraded — fix the DB state and restart; no separate manual migration step is needed on a clean first install.

---

## Smoke test

From the repo root (requires GNU Make):

```bash
make health
```

This calls **`GET http://localhost:8000/health`** and pretty-prints JSON.

Without Make:

```bash
docker compose ps
curl -fsS http://localhost:8000/health
```

All core services should show **healthy** (or **running** for short-lived helpers). **`curl`** should return HTTP 200.

---

## Common errors

| Symptom | What to do |
| --- | --- |
| **Port already in use** | Another process owns **80**, **8000**, **5432**, or **11434**. Run `docker compose ps`, stop conflicting stacks, or change host port mappings in `.env` / `docker-compose.yml` comments. |
| **Insufficient RAM / Ollama OOM** | Default models need ~8 GB RAM. Reduce `OLLAMA_EXTRA_MODELS`, disable optional reranker (`RERANKER_BACKEND=none` is the default), add swap, or wait for first pulls to finish before load-testing chat. |
| **Ollama not running / model not pulled** | Check `docker compose ps ollama` and `docker compose logs ollama orchestrator`. Pull models manually (see **Pull the default Ollama model** above) or restart after Ollama is healthy. |

For more issues, see **`docs/guides/troubleshooting.md`**.

---

## Next steps

For HTTPS and access from phones or laptops off your LAN (Tailscale Serve, Cloudflare Tunnel), see **[Remote access](remote-access.md)**.
