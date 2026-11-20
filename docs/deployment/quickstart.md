# First-run quickstart

Install Lumogis from the published images in an empty folder, start it with Docker Compose, and confirm it is healthy. You need two downloaded files and no source code. Updating later is two commands: see [Upgrading](upgrading.md).

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

   No edits are required. Sign-in is on, and Lumogis generates its secrets on the first start and saves them in `.env`. **Create the admin account straight after you start Lumogis (step 4).** Until you do, anyone who can reach this computer can create it. See [Sign-in](#sign-in).

3. **Start Lumogis**

   ```bash
   docker compose up -d
   ```

   Compose pulls the published images; nothing is built. First boot can take several minutes (image pull, database set-up, Ollama model downloads). Run Compose from this folder: it tells the restart service where the install lives.

4. **Open the UI**

   Open **http://localhost/** (Caddy → Lumogis Web) **now** and create the admin account: enter an email and a password (at least 12 characters) twice. You are signed in as the household admin, and an empty chat links to adding your first documents. If you see a sign-in page instead of **Create the admin account**, someone else created the admin first: see [If someone else created the admin account first](#if-someone-else-created-the-admin-account-first).

   Orchestrator API/Swagger is also available at **http://localhost:8000/docs** if you need it, on the host only: port 8000 is published on `127.0.0.1` and is not reachable from other machines. Use the Caddy URL (port 80) from other devices.

   **Runs as:** the orchestrator runs as the owner of the install folder, so files it writes in `.env` and `ai-workspace/` belong to you. Set `LUMOGIS_RUN_UID` / `LUMOGIS_RUN_GID` in `.env` to choose another user (`0` keeps root). Under rootless Docker, Podman or a user-namespace remap nothing changes. `docker compose exec orchestrator ...` still enters as root.

**Updating:** `docker compose pull && docker compose up -d`, from the same folder. Take a backup first and read the release notes: [Upgrading](upgrading.md).

**Staying on one version:** set `LUMOGIS_VERSION=0.13.0` (no leading `v`) in `.env`. The default, `release`, follows the latest release.

### For contributors: run from a git checkout

To change Lumogis, or to use LibreChat or the GPU or LiteLLM overlays (not in the release files yet), run from the source instead. The repository's `docker-compose.yml` **builds** the Lumogis images from your checkout; it needs Git and, for `make`, GNU Make.

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

<a id="turn-on-sign-in"></a>

## Sign-in

Sign-in is on in a new install. There is nothing to switch on and nothing to type into `.env`.

1. **The first admin.** The first person to open Lumogis in a browser sees **Create the admin account**. They enter an email (an ordinary address such as `name@example.com`; names ending in `.local` or `.test` are not accepted) and a password of at least 12 characters, twice, and are signed in as the household admin. This can be done only once. **Do it straight after `docker compose up -d`:** until the admin exists, anyone who can reach this computer can create it. Lumogis logs a reminder every five minutes while it waits (`docker compose logs orchestrator`).
2. **Invite the household.** Open **Settings → Members and invites** and create an invite link for each person. Open Lumogis at the computer's network address (for example `http://192.168.1.20`), not at `localhost`, so the links you copy work on other devices. Phones and laptops on your home network can sign in over plain HTTP at that address.
3. **`.env` holds generated secrets, so keep a copy.** On the first start Lumogis writes `AUTH_SECRET`, the refresh secret and `LUMOGIS_CREDENTIAL_KEY` into `.env`. The credential key decrypts the passwords you save in Lumogis (for example for email): if you lose it, saved passwords cannot be read and must be entered again. Keep a copy of `.env` with your backups (see [Backup and restore](../guides/backup-restore.md)), and keep the file readable only by you. Lumogis never replaces a secret that is already set.
4. **A lost admin password.** Another admin can reset it from **Settings → Members and invites**. If you are the only admin, run `python -m scripts.reset_password` from the `orchestrator/` directory inside the container (`docker compose exec orchestrator python -m scripts.reset_password`); see the script's docstring.
5. **`AUTH_ENABLED=false` is for development only.** It turns sign-in off for developers and tests. Lumogis Web cannot be used with it: the sign-in page says so and asks you to set `AUTH_ENABLED=true`.
6. **A public name or a tunnel.** Lumogis accepts the address in the browser, so you do not need to set an address. To create the admin through a public name or a tunnel, set `LUMOGIS_PUBLIC_ORIGIN` in `.env` first, or create the admin at the computer's network address. Email password reset and emailed links also need `LUMOGIS_PUBLIC_ORIGIN`.

Scripted installs can still set `LUMOGIS_BOOTSTRAP_ADMIN_EMAIL` and `LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD` (or `LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD_FILE`) in `.env`: the admin is then created at start and the setup page never appears.

### If someone else created the admin account first

If you open Lumogis and see a sign-in page instead of **Create the admin account**, and you did not create the admin, someone else on your network got there first. Remove the install and start again:

1. Stop Lumogis and delete its data volumes: `docker compose down -v`.
2. Delete the install's `ai-workspace/` folder (and `lumogis-data/` if you let Lumogis create it), because files could have been uploaded.
3. Delete `.env` and copy it again (`cp .env.example .env` from a checkout, or download `example.env` as `.env` again), so new secrets are generated.
4. Run `docker compose up -d`, then open Lumogis straight away and create the admin account.

For reaching Lumogis safely from outside your network, see [Remote access](remote-access.md).

---

<a id="speech-to-text"></a>

## Speech-to-text

The **Dictate** button in chat and in the email composer turns speech into text. In an install from the Release files it is **on from the first start**: the files carry a small speech engine (Speaches, MIT-licensed, running on your computer) and `example.env` switches it on with `COMPOSE_PROFILES=stt`. Audio never leaves your computer.

**Where the microphone works.** Browsers give a page the microphone only over HTTPS or at `localhost`. On a new install that means **on the install computer, at `http://localhost`**. On phones and other computers, Dictate works once **Settings → Home address** is on ([Secure address at home](remote-access.md#secure-address-at-home)). Until then, on a plain `http://` address, the button is greyed out and says so.

**What it costs.**

- **Download:** the speech engine is about **0.85 GB** (0.55 GB on an arm64 computer such as an Apple Silicon Mac), so the first `docker compose up -d` pulls about 1.1 GB more than without it.
- **Disk:** about **3.3 GB**, or **2.5 GB** on arm64 (the engine image takes about 3 GB once unpacked, 2.2 GB on arm64; the speech model about 0.28 GB).
- **Memory:** up to about **0.6 GB** while it transcribes, less when idle (about 0.2 GB on arm64 after a transcription).
- **One more connection:** the speech engine fetches the speech model from `huggingface.co` once, the first time Lumogis installs it (and again if you reset it), and checks it for updates when it starts. Nothing else, and never your audio; see [TELEMETRY.md](../../TELEMETRY.md).

**First use.** Lumogis installs the speech model itself, a few minutes after the first start (longer on a slow connection). If you press Dictate before it is ready, Lumogis says *"The speech model is still downloading. Try again in a minute."* Admins can see the state in **Settings → Diagnostics** (`speech_to_text.model_state`: `ready` when done).

**English only by default.** The default model, `Systran/faster-whisper-base.en`, understands English. For other languages set `STT_MODEL=Systran/faster-whisper-base` in `.env` and run `docker compose up -d`; Lumogis installs the new model.

**Turn it off.** Take `stt` out of `COMPOSE_PROFILES` in `.env`, set `STT_BACKEND=none`, then run:

```bash
docker compose stop lumogis-stt && docker compose rm -f lumogis-stt
docker compose up -d
```

(`docker compose up -d` alone leaves the container running.) The microphone button then no longer shows in chat. To get the disk back, delete the model volume: `docker volume rm <project>_lumogis_stt_models` (the project is `lumogis` unless you changed `COMPOSE_PROJECT_NAME`) and `docker image prune`.

**If dictation keeps failing after the state says `ready`,** the speech model may be damaged (for example after a download that was cut off). Reset it; Lumogis installs it again:

```bash
docker compose stop lumogis-stt && docker compose rm -f lumogis-stt
docker volume rm lumogis_lumogis_stt_models      # <project>_lumogis_stt_models
docker compose up -d
```

**Source checkouts** keep speech-to-text opt-in: add `docker-compose.stt.yml` to `COMPOSE_FILE` and set the `STT_*` lines described in `.env.example`.

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

## Turn on the knowledge graph

The knowledge graph is optional and off by default. Lumogis learns the people, places, organisations and projects in your documents and chats whether the graph is on or off, and search and chat work without it. The graph adds the links between them, so chat can use how people, projects and documents connect as context.

**What it needs.** Two more containers: FalkorDB (the graph database) and the Lumogis graph service. Together they need about 2 GB more memory. Lumogis itself needs about 8 GB, so plan on about 10 GB in total. If you use Docker Desktop, that is the memory Docker Desktop may use (Settings → Resources). Settings → Knowledge graph tells you what Lumogis reads on this machine.

### From the Release files

If you installed from the two Release files (`docker-compose.yml` and `example.env`), open **Settings → Knowledge graph** and choose **Turn on**. Lumogis writes the settings into `.env`, downloads the graph images (about 1 GB the first time, so check your free disk space), starts the two containers and restarts itself for a moment. The page says "The knowledge graph is on." once Lumogis can reach the graph service; allow up to five minutes. If the page says this machine is short of memory, you can still turn it on after you confirm that you understand; if Lumogis then becomes slow or the graph keeps stopping, memory is the first thing to check, and **Turn off** undoes it. **Turn off** stops the two containers and keeps the graph's data, so turning it on again picks up where it left off.

The button needs the `docker-compose.yml` of a Release that includes the knowledge graph. If the page says your file is older, download the `docker-compose.yml` of the release you run (keep your `.env`) and run `docker compose up -d`.

To do it by hand instead, remove the `#` from the knowledge graph lines at the end of your `.env` (they are in `example.env`), put a long random value in `GRAPH_WEBHOOK_SECRET` (`openssl rand -hex 32`), and run `docker compose up -d`. To turn it off by hand, set `GRAPH_MODE=disabled`, take `graph` out of `COMPOSE_PROFILES` and the graph URL out of `CAPABILITY_SERVICE_URLS`, then run `docker compose stop falkordb lumogis-graph && docker compose rm -f falkordb lumogis-graph && docker compose up -d --force-recreate --no-deps backup orchestrator` (Lumogis and the backup job read these settings when they are created).

### From a source checkout

On a git checkout the graph image is built on your machine, so there is no button: use these steps.

1. **Set these in `.env`.** If `COMPOSE_FILE` already lists other files (for example `docker-compose.gpu.yml`), keep them and add the two graph files to the end. If `CAPABILITY_SERVICE_URLS` already has a value, add the URL after a comma.

   ```
   COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml
   GRAPH_MODE=service
   KG_SERVICE_URL=http://lumogis-graph:8001
   GRAPH_WEBHOOK_SECRET=<a long random value>
   CAPABILITY_SERVICE_URLS=http://lumogis-graph:8001
   ```

   Make the secret with `openssl rand -hex 32`.
2. **Start it.** Run `docker compose up -d` in the Lumogis folder. Do not use `make dev`: it ignores `COMPOSE_FILE`. The first start builds the graph service and takes a few minutes.
3. **Check it.** Open **Settings → Knowledge graph**. It says "The knowledge graph is on." once Lumogis can reach the graph service; allow up to five minutes after the start.

On a small test machine you can cap the graph service's memory with `KG_MEM_LIMIT=1g` in `.env`. Do this for testing only: a large graph needs the full 4 GB.

**Turn it off again.** Set `GRAPH_MODE=disabled`, take the two graph files out of `COMPOSE_FILE`, and run `docker compose up -d --remove-orphans`.

`GRAPH_MODE=service` is the only mode that works on the standard Lumogis image.

---

## Next steps

For HTTPS and access from phones or laptops off your LAN (Tailscale Serve, Cloudflare Tunnel), see **[Remote access](remote-access.md)**.
