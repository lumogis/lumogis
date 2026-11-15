# First-run quickstart

Install Lumogis from the published images in an empty folder, start it with Docker Compose, and confirm it is healthy. Like Immich, you need two downloaded files and no source code. Updating later is two commands: see [Upgrading](upgrading.md).

---

## Prerequisites

- Docker and Docker Compose installed (Docker Engine + Compose v2, or Docker Desktop). Intel/AMD (x86-64) and ARM (arm64, including Apple Silicon) machines both work.
- 8 GB RAM minimum (default Ollama embedder + chat model). With Docker Desktop, give it at least 8 GB in **Settings → Resources**.

Before you start, open a **new** terminal and check that Docker answers:

```bash
docker version
docker compose version
```

Both should print version information, and `docker version` should show a **Server** section (Docker is running). On a Mac, if the shell says `command not found: docker` after installing Docker Desktop, start Docker Desktop once, then open a new terminal. If it is still not found, Docker Desktop installed its commands in your home folder: add them to your shell with `echo 'export PATH="$HOME/.docker/bin:$PATH"' >> ~/.zprofile` and open a new terminal, or choose **System** under Docker Desktop **Settings → Advanced**.

---

## Quick start (4 steps)

1. **Make a folder for Lumogis and go into it**

   ```bash
   mkdir lumogis && cd lumogis
   ```

   Your documents, uploads and backups live in folders next to the two files (`lumogis-data/`, `ai-workspace/`), unless you point them elsewhere in `.env`.

2. **Download the two files from the latest release**

   ```bash
   curl -fsSL -o docker-compose.yml https://github.com/lumogis/lumogis/releases/latest/download/docker-compose.yml
   curl -fsSL -o .env https://github.com/lumogis/lumogis/releases/latest/download/example.env
   ```

   (`wget -O docker-compose.yml …` works too, or download both from the [Releases page](https://github.com/lumogis/lumogis/releases/latest) and save `example.env` as `.env`.)

   No edits are required for a single-person trial. Sign-in stays off: Lumogis Web opens straight into Lumogis as its only user, with admin rights. Anyone who can reach this machine's address can do the same, so turn sign-in on (see [Turn on sign-in](#turn-on-sign-in)) before other people share the network or you publish Lumogis beyond it.

3. **Start Lumogis**

   ```bash
   docker compose up -d
   ```

   Compose pulls the published images; nothing is built. First boot can take several minutes (image pull, database set-up, Ollama model downloads). Run Compose from this folder: it tells the restart service where the install lives.

4. **Open the UI**

   Open **http://localhost/** (Caddy → Lumogis Web). Chat opens directly. A banner says that sign-in is off, and an empty chat links to adding your first documents.

   Orchestrator API/Swagger is also available at **http://localhost:8000/docs** if you need it, on the host only: port 8000 is published on `127.0.0.1` and is not reachable from other machines. Use the Caddy URL (port 80) from other devices.

   **Runs as:** the orchestrator runs as the owner of the install folder, so files it writes in `.env` and `ai-workspace/` belong to you. Set `LUMOGIS_RUN_UID` / `LUMOGIS_RUN_GID` in `.env` to choose another user (`0` keeps root). Under rootless Docker, Podman or a user-namespace remap nothing changes. `docker compose exec orchestrator ...` still enters as root.

**Updating:** `docker compose pull && docker compose up -d`, from the same folder. Take a backup first and read the release notes: [Upgrading](upgrading.md).

**Staying on one version:** set `LUMOGIS_VERSION=0.13.0` (no leading `v`) in `.env`. The default, `release`, follows the latest release.

### For contributors: run from a git checkout

To change Lumogis, or to use LibreChat or the GPU, speech or LiteLLM overlays (not in the release files yet), run from the source instead. The repository's `docker-compose.yml` **builds** the Lumogis images from your checkout; it needs Git and, for `make`, GNU Make.

```bash
git clone https://github.com/lumogis/lumogis
cd lumogis
cp .env.example .env
docker compose up -d
```

Update a checkout with `git pull && make update`. To pull the published images while keeping the checkout, set **`COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`** in `.env` (see the comments at the top of `.env.example`).

---

## Pull the default Ollama model

On first start, the orchestrator entrypoint (`orchestrator/docker-entrypoint.sh`) waits for Ollama and pulls:

- **`nomic-embed-text`** — embedding model (`EMBEDDING_MODEL` in `.env`)
- **`llama3.2:3b`** — default chat model (`OLLAMA_EXTRA_MODELS`; add it to `.env` to choose others, see `.env.example` on GitHub)

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

## Turn on sign-in

A default install has sign-in off: it is a single-user Lumogis, and anyone who can reach its address uses it as the admin. Turn sign-in on before other people can reach it (your household network, a phone, a public hostname). While sign-in is off and `CADDY_DOMAIN` names a public hostname, the Caddy container logs a warning each time it starts (`docker compose logs caddy`).

0. **Before you switch:** let any captures queued offline on each device sync, or discard them. After the switch they are no longer listed or synced. Do not create users while sign-in is off (Lumogis Web hides that page for this reason): the next restart would refuse to start.
1. **Set these in `.env`:**
   - `AUTH_ENABLED=true`
   - `AUTH_SECRET` — a random secret, for example `openssl rand -hex 32`.
   - `LUMOGIS_CREDENTIAL_KEY` — a Fernet key, generated with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` (a plain hex string starts but fails the first time a credential is stored). **Back it up** with your database backups: losing it makes every stored credential unreadable.
   - `LUMOGIS_BOOTSTRAP_ADMIN_EMAIL` and `LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD` — the first admin account; the password needs at least 12 characters.
   - `LUMOGIS_PUBLIC_ORIGIN` — the exact address people type in the browser, for example `http://localhost` or `https://lumogis.example.com`.

   The refresh-token secret is generated by the container entrypoint if it is missing.
2. **Restart with the new settings:**

   ```bash
   docker compose up -d
   ```

   Lumogis refuses to start, with a message naming the setting, if `AUTH_SECRET`, `LUMOGIS_CREDENTIAL_KEY` or the first admin account is missing. `LUMOGIS_PUBLIC_ORIGIN` defaults to `http://localhost`; if people reach Lumogis at another address, set it, or sign-in from the browser is rejected.
3. **What happens:** on that first start with sign-in on, everything Lumogis stores per person (documents, chats, memories, entities, settings) moves to the admin account. What each browser kept locally (drafts, chat history cached on that device) is cleared at the first sign-in, and the dismissed "try asking" card may show once more. Open Lumogis Web and sign in with the admin email and password.
4. **Invite the rest of the household** from **Admin → Users**.
5. Turning sign-in off again does not move the data back: the single-user view then starts empty.

For reaching Lumogis safely from outside your network, see [Remote access](remote-access.md).

---

## Verify Postgres migrations ran

On a new, empty database the orchestrator creates the schema from **`postgres/init.sql`** (baked into its image), then applies every migration. Later schema changes are applied **automatically** on each orchestrator start by **`python3 /app/db_migrations.py`** (idempotent; tracks applied files in `schema_migrations`).

Confirm in logs:

```bash
docker compose logs orchestrator | grep -i migration
```

You should see `[entrypoint] Running Postgres migrations...` without a fatal error; a first start also logs `baseline_applied`. If a migration fails, the entrypoint logs a WARNING and the service may be degraded — fix the DB state and restart; no separate manual migration step is needed on a clean first install. If the schema cannot be created at all on an empty database, the orchestrator stops with `[entrypoint] FATAL: no database schema` instead of starting half-working.

---

## Smoke test

From the install folder:

```bash
docker compose ps
curl -fsS http://localhost:8000/health
```

All core services should show **healthy** (or **running** for short-lived helpers). **`curl`** should return HTTP 200. In a git checkout, `make health` does the same and pretty-prints the JSON.

---

## Common errors

| Symptom | What to do |
| --- | --- |
| **Port already in use** (`failed to bind host port … address already in use`) | The stack publishes **80** and **443** (Caddy), **8000** (orchestrator, `127.0.0.1` only) and **6334** (Qdrant, `127.0.0.1` only); Postgres and Ollama are not published, so other databases or an Ollama already on this machine do not clash. Find the owner with `sudo ss -ltnp | grep -E ':(80|443|8000|6334) '`, stop it, or move Lumogis in `.env`: `CADDY_HTTP_HOST_PORT`, `CADDY_HTTPS_HOST_PORT`, `ORCHESTRATOR_HOST_PORT`, `QDRANT_HOST_PORT`. Then `docker compose up -d` again. |
| **Insufficient RAM / Ollama OOM** | Default models need ~8 GB RAM. Reduce `OLLAMA_EXTRA_MODELS`, disable optional reranker (`RERANKER_BACKEND=none` is the default), add swap, or wait for first pulls to finish before load-testing chat. |
| **Ollama not running / model not pulled** | Check `docker compose ps ollama` and `docker compose logs ollama orchestrator`. Pull models manually (see **Pull the default Ollama model** above) or restart after Ollama is healthy. |

For more issues, see **`docs/guides/troubleshooting.md`**.

---

## Next steps

For HTTPS and access from phones or laptops off your LAN (Tailscale Serve, Cloudflare Tunnel), see **[Remote access](remote-access.md)**.
