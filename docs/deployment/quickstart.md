# First-run quickstart (published images)

This guide is the canonical **≤5-step** path for a new self-hoster using **pre-built images** from GitHub Container Registry (GHCR). It mirrors the commands in the root **[`README.md`](../../README.md)** §Getting started so the two stay aligned.

---

## Goal

Run Lumogis on your machine with **Docker Compose**, using **`docker-compose.yml`** plus **`docker-compose.ghcr.yml`**, then open the web UI and confirm the stack is healthy.

**What “complete” means here:** the **Lumogis Web** UI is reachable at **`http://localhost/`** (Caddy front door), and optionally you confirm **`GET /health`** on the orchestrator at **`http://localhost:8000/health`**.

---

## Prerequisites

- **Docker Desktop 4.x+** or **Docker Engine** with **Compose v2.x** (required for the GHCR overlay that uses `build: !reset null` — see comments in **`docker-compose.ghcr.yml`**).
- **Git** (to clone the official repo).
- **Rough sizing:** allow **several minutes** on first start for image pulls and the default **Ollama** models; plan for **at least ~8 GB RAM** for the default embedder + small chat model.
- **Disk:** images and model layers need **several GB** of free space; if pulls fail, check free disk and Docker’s data root.

Obtain Compose and env files only from the **official Lumogis repository** (`https://github.com/lumogis/lumogis`) or a **trusted fork** you audit yourself. Do **not** paste real API keys into chat logs, tickets, or git commits; keep secrets in **`.env`** (gitignored).

---

## Steps

### 1. Clone the repository

```bash
git clone https://github.com/lumogis/lumogis.git
cd lumogis
```

### 2. Create your environment file

```bash
cp .env.example .env
```

Edit **`.env`** as needed:

- Set **`LUMOGIS_PUBLIC_ORIGIN`** to the URL family members use in the browser (see **`.env.example`** comments), especially when **`AUTH_ENABLED=true`**.
- Leave **`AUTH_ENABLED=false`** for a simple LAN trial unless you intentionally want login flows — see **Auth and credential keys** below.

Never commit **`.env`**.

### 3. Start the stack (GHCR images)

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml \
  docker compose up -d --pull always
```

Images are pulled from **`ghcr.io/lumogis/`** (public packages — no registry login required when package visibility is **Public**).

Optional: pin a release (omit a leading **`v`** on the tag):

```bash
IMAGE_TAG=1.2.3 COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml \
  docker compose up -d --pull always
```

> **GHCR visibility:** after the first workflow publish, each new package may default to private. If **`docker pull`** returns **403** or **not found**, open the repository **Packages** page, open **lumogis-orchestrator** / **lumogis-web**, and set visibility to **Public** (same wording as **`README.md`**).

### 4. Wait for first boot

The first **`docker compose up`** can take **several minutes**: image layers, Postgres init, and **Ollama** model pulls (see next section). Watch progress:

```bash
docker compose logs -f orchestrator
```

When logs settle, continue.

### 5. Open the UI and run a smoke check

- **Web UI (recommended):** **`http://localhost/`** — Lumogis Web behind Caddy (same-origin for cookies and API routes).
- **Orchestrator directly:** **`http://localhost:8000`** — Swagger at **`/docs`**.
- **Health check (fail-fast `curl`):**

```bash
curl -fsS http://localhost:8000/health | python3 -m json.tool
```

**`-fsS`** makes **`curl`** exit non-zero on HTTP errors, which is useful in scripts. From a dev checkout with **GNU Make**, you can instead run **`make health`**, which uses **`curl -s`** (no **`-f`**) and pretty-prints JSON — behaviour differs slightly if the endpoint returns an error status.

**Verify the full stack (read-only):** from the repo root, **`make doctor`** runs host-side Compose + HTTP probes (orchestrator **`/healthz`**, optional Caddy edge) without importing the orchestrator. Machine-readable JSON (stable v1 schema for dashboards / evidence bundles): **`make doctor ARGS="--json"`** (requires **`jq`**). Security audits (**`npm audit` / `pip-audit` / Bandit**) are **opt-in**: **`make doctor ARGS="--security"`** — expect network traffic and long cold-cache runs; see **`scripts/doctor/README.md`**.

---

## Ollama models on first start

The orchestrator **`docker-entrypoint.sh`** (see **`orchestrator/docker-entrypoint.sh`** in the repo):

1. Waits for **Ollama** at **`OLLAMA_URL`** (default **`http://ollama:11434`**).
2. Ensures **`EMBEDDING_MODEL`** is present (default **`nomic-embed-text`** in **`.env.example`**), pulling it if missing.
3. Pulls each name in **`OLLAMA_EXTRA_MODELS`** **after** the embedder. Names are **comma-separated** only (the entrypoint splits on **`,`**; spaces as separators between multiple models are **not** supported). The default in **`.env.example`** is **`llama3.2:3b`**. Set **`OLLAMA_EXTRA_MODELS=`** (empty) to skip extra pulls.

If a pull fails, the entrypoint logs a **WARNING** and **continues** — the orchestrator may start **degraded** (e.g. chat or ingest unavailable until you fix models). Recover with **`docker compose exec ollama ollama pull <name>`** or **Settings → Models** in the web UI.

---

## Postgres schema migrations

Schema changes after the initial **`postgres/init.sql`** run are applied on **orchestrator** startup by **`python3 /app/db_migrations.py`** (idempotent; tracks applied files).

If the migration runner exits **non-zero**, the entrypoint logs a **WARNING** and **continues** — the service may be **degraded**. Check orchestrator logs, fix SQL/DB state, and restart; do **not** assume migrations silently succeeded.

---

## Auth and credential keys

When **`AUTH_ENABLED=true`**, the entrypoint **refuses to start** if **`AUTH_SECRET`** or **`LUMOGIS_CREDENTIAL_KEY`** / **`LUMOGIS_CREDENTIAL_KEYS`** are missing or still set to placeholders from **`.env.example`** (see **`orchestrator/docker-entrypoint.sh`** fatal messages). Generate a Fernet key with the **`python3 -c "from cryptography.fernet import Fernet; ..."`** one-liner shown in the entrypoint log and in **`.env.example`** comments.

---

## LibreChat profile

If **`.env`** sets **`COMPOSE_PROFILES`** to include **`librechat`**, the legacy LibreChat UI is typically at **`http://localhost:3080`** (see root **`README.md`**). The primary path above remains **Lumogis Web** at **`http://localhost/`**.

---

## Common errors and fixes

| Symptom | What to check |
| --- | --- |
| **Port already in use** | Another service owns **80**, **8000**, **5432**, **11434**, etc. Run **`docker compose ps`**, stop conflicting stacks, or override host ports via **`.env`** / Compose (see **`docker-compose.yml`** comments and general Compose docs). |
| **Very slow first start / OOM** | First **Ollama** pulls are large; RAM below ~8 GB with defaults may thrash. Reduce models or add swap; wait for pulls to finish. |
| **GHCR pull 403 / manifest unknown** | Package visibility still **Private**, wrong **`IMAGE_TAG`**, or transient registry issue — see the GHCR note in step 3. |
| **`curl` health check fails** | Orchestrator not ready, wrong port, or TLS/proxy in front of **8000**. Inspect **`docker compose logs orchestrator`**. |
| **Docker daemon errors** | Run **`docker info`** from the same shell; ensure the daemon is running and your user can access it. |
| **Disk full during pull** | Free space on Docker’s storage driver volume; remove unused images **`docker system prune`** (careful — removes unused data). |

For deeper operational issues, see **[`docs/guides/troubleshooting.md`](../guides/troubleshooting.md)**.

---

## Platform notes

- **Ubuntu 22.04 / 24.04 LTS** (**x86_64** or **aarch64**): the bash snippets above are copy-pasteable.
- **macOS (Apple Silicon)**: same commands; Docker Desktop must be running.
- **macOS (Intel)**: best-effort — same Compose pattern; if something breaks, compare with Docker Desktop release notes for your chip.
- **Windows (PowerShell)**: the GHCR **`COMPOSE_FILE=... docker compose ...`** line is the same env assignment pattern as in **`README.md`**’s Windows contributor clone snippet; use **`;`** between statements when pasting into PowerShell, e.g. **`cd $HOME\lumogis; $env:COMPOSE_FILE='docker-compose.yml:docker-compose.ghcr.yml'; docker compose up -d --pull always`**. For full Windows contributor flows, follow **`README.md`** and **`CONTRIBUTING.md`**.

---

## Next steps

- **Remote access** (Tailscale, Cloudflare Tunnel, hardening for exposure beyond the LAN): planned doc path **`docs/deployment/remote-access.md`** — tracked as **LUM-158** (not shipped yet in this tree; do not assume the file exists until that issue closes).
- **Build from source / CI-style checks:** **[`CONTRIBUTING.md`](../../CONTRIBUTING.md)**.
