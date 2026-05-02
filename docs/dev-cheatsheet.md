# Dev Deployment Cheat Sheet — Docker & Git

> Status: Active  
> Last reviewed: 2026-05-02  
> Verified against commit: 98f02b1  
> Owner: Docs Librarian

A practical, paste-ready reference for working on the **lumogis** repository
(`lumogis/lumogis`). Covers first-time setup, the day-to-day "I changed a file,
now what?" loop, and the Git workflow to ship those changes.

> **Companion doc:** [`connect-and-verify.md`](./connect-and-verify.md) — a
> linear runbook to stand up **Core + Lumogis Web (Caddy) + optional LibreChat + optional lumogis-graph + FalkorDB + storage** and prove every connection end-to-end.
> Use that when you want a single "connect everything and test" walkthrough.

> Automated test layers: [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md).

> Run all `docker compose ...` commands from the **repository root** (where
> `docker-compose.yml` and `.env` live). Replace `/path/to/lumogis` below with
> your own clone path when copying examples.

---

## 0. TL;DR — The Three Commands You'll Use Most

```bash
docker compose up -d --build orchestrator    # rebuild + restart one service after a code change
docker compose logs -f orchestrator          # follow its logs
docker compose restart orchestrator          # restart without rebuilding (config / env changes only)
```

That covers ~80% of dev work. Everything below is the long form.

---

## 1. First-Time Setup

```bash
git clone https://github.com/lumogis/lumogis.git
cd lumogis
cp .env.example .env                 # then edit .env (API keys, FILESYSTEM_ROOT, etc.)
docker compose up -d --build         # build all images and start the stack
```

Wait ~2–5 min for first boot (Ollama pulls models, BGE reranker may download).
Then verify:

```bash
make health                          # GET http://localhost:8000/health
# or
curl -s http://localhost:8000/health | python3 -m json.tool
```

URLs once the stack is up:

| Service        | URL                              |
| -------------- | -------------------------------- |
| LibreChat UI   | http://localhost:3080            |
| Orchestrator   | http://localhost:8000            |
| Dashboard      | http://localhost:8000/dashboard  |
| Qdrant         | http://localhost:6333/dashboard  |

---

## 2. The Daily Dev Loop — "I Changed a File, Now What?"

The right command depends on **what** you changed. Use this table:

| What you changed                                       | Command                                                                   |
| ------------------------------------------------------ | ------------------------------------------------------------------------- |
| Python code in `orchestrator/` (built into the image)  | `docker compose up -d --build orchestrator`                               |
| Python code, want hot-reload while iterating           | `make dev` (uses `docker-compose.dev.yml`, mounts `./orchestrator:/app`)  |
| `requirements.txt` or `Dockerfile`                     | `docker compose build --no-cache orchestrator && docker compose up -d`    |
| `.env` (env vars)                                      | `docker compose up -d` (Compose detects env diff and recreates)           |
| `config/librechat.yaml` or branding files              | `docker compose restart librechat`                                        |
| `docker-compose*.yml` itself                           | `docker compose up -d` (re-applies the diff)                              |
| Just want to bounce a service                          | `docker compose restart <service>`                                        |
| `stack-control/` code                                  | `docker compose up -d --build stack-control`                              |
| `services/lumogis-graph/` code                         | `docker compose up -d --build lumogis-graph` (if enabled via overlay)     |

### 2a. Iterating fast on orchestrator code (`make dev`)

```bash
make dev
```

This runs `docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f
docker-compose.dev.yml up --build --pull always` — it bind-mounts
`./orchestrator` into the container and starts uvicorn with `--reload`. Edit a
`.py` file on the host and the server reloads automatically. No rebuild needed
until you change `requirements.txt`.

Stop it with `Ctrl-C` (it runs in the foreground). To go back to the regular
detached stack: `docker compose up -d`.

### 2b. Why `up -d --build` vs `restart` vs `build`

- `restart <svc>` — stops and starts the **existing container**. No rebuild, no
  config re-read from compose files. Use for "kick it" / read new env vars from
  `.env`.
- `up -d --build <svc>` — rebuilds the image if anything in the build context
  changed, then **recreates** the container if the image or config changed.
  This is the workhorse.
- `build <svc>` — only builds the image. Doesn't touch the running container.
  Useful in CI; rarely needed locally on its own.
- `up -d --force-recreate <svc>` — recreate the container even if nothing
  changed (e.g., to re-run init logic).

---

## 3. Docker Cheat Sheet

### Lifecycle

```bash
docker compose up -d                         # start everything in background
docker compose up -d <service>               # start one service (and its deps)
docker compose down                          # stop and REMOVE containers (keeps volumes)
docker compose down -v                       # ALSO delete named volumes (DESTRUCTIVE — wipes Postgres, Qdrant, Mongo, models)
docker compose stop                          # stop containers, keep them around
docker compose start                         # start previously stopped containers
docker compose restart <service>             # bounce one service
docker compose ps                            # what's running, health status, ports
docker compose ps -a                         # include stopped
```

### Building

```bash
docker compose build                         # build all services that have a `build:` block
docker compose build orchestrator            # build just one
docker compose build --no-cache orchestrator # ignore the layer cache (use after weird dep issues)
docker compose build --pull orchestrator     # also pull fresh base images (FROM ...)
```

### Logs & inspection

```bash
docker compose logs                          # all services, dump and exit
docker compose logs -f                       # follow all
docker compose logs -f --tail 100 orchestrator
make logs                                    # alias: follow last 50 lines of orchestrator
docker compose top                           # running processes per service
docker stats                                 # live CPU/RAM per container
docker compose config                        # show fully-resolved compose file (after env + overrides)
```

### Exec into a container

```bash
docker compose exec orchestrator bash        # interactive shell in the running container
docker compose exec orchestrator python -c "import sys; print(sys.version)"
docker compose exec postgres psql -U lumogis lumogis
docker compose exec mongodb mongosh
```

If the container isn't running, use `run --rm` for a one-shot:

```bash
docker compose run --rm orchestrator bash
```

### Volumes (where state lives)

```bash
docker volume ls                             # list all volumes on the host
docker volume ls | grep lumogis              # just this project's
docker volume inspect lumogis_postgres_data
docker compose down -v                       # nuke everything for a clean slate (DESTRUCTIVE)
```

Named volumes used by this stack (see `docker-compose.yml` bottom):
`mongodb_data`, `ollama_data`, `qdrant_data`, `postgres_data`, `hf_cache`.

### Cleaning up disk

```bash
docker system df                             # how much space is Docker using?
docker image prune                           # remove dangling images
docker image prune -a                        # remove ALL unused images (frees lots of GB)
docker container prune                       # remove stopped containers
docker builder prune                         # clear BuildKit cache (often the biggest hog)
docker system prune -a --volumes             # nuclear option (DESTRUCTIVE — also removes unused volumes)
```

### Compose file overlays (this repo uses several)

`COMPOSE_FILE` in `.env` selects which overlays are active. Examples:

```bash
# Enable the GPU overlay
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml

# Enable FalkorDB (knowledge graph) and LiteLLM
COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.litellm.yml
```

Or pass on the command line for a single command:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml docker compose up -d
```

The auto-generated `docker-compose.override.yml` is always picked up by
Compose (Compose's default behaviour) unless you pass `-f` explicitly.

---

## 4. Testing Cheat Sheet

**Docker / Compose (no local Python venv):** The orchestrator **production image does not ship pytest**. `make compose-test` and similar targets run `pip install -q -r requirements-dev.txt` **inside the container** first, then `python -m pytest`. Do **not** use a bare `docker compose run … orchestrator pytest` — pytest may be absent.

```bash
make compose-lint                  # ruff (orchestrator; installs dev deps in container)
make compose-test                  # orchestrator unit tests (dev deps + pytest in container)
make compose-test-stack-control    # stack-control unit tests
make compose-test-integration      # integration tests (stack running; FalkorDB overlay in Makefile)
make compose-test-kg               # lumogis-graph KG service tests (dedicated test image)
```

**Local venv** (see `CONTRIBUTING.md`): `make lint`, `make test`, `make test-integration`, **`make test-graph-parity`** (slow; local pytest + Docker for the stack — not a `compose-test-*` target).

**Lumogis Web — OpenAPI:** `make web-codegen` regen from committed snapshot. `make web-codegen-check` / `npm run codegen:check` compares to a **live** `openapi.json` (`LUMOGIS_OPENAPI_URL`, default `http://localhost:8000/openapi.json`) — orchestrator must be up. **Refresh committed snapshot** after `/api/v1/*` route changes (repo root): `cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys --out ../clients/lumogis-web/openapi.snapshot.json`.

Run a single orchestrator test file via Compose (full pattern):

```bash
docker compose run --rm -w /project/orchestrator orchestrator sh -c \
  "pip install -q -r requirements-dev.txt && python -m pytest tests/path/to/test_x.py -v"

docker compose run --rm -w /project/orchestrator orchestrator sh -c \
  "pip install -q -r requirements-dev.txt && python -m pytest tests -k 'name_of_test' -v"
```

---

## 5. Git Cheat Sheet

### Remotes in this repo

```
origin   → https://github.com/<you>/lumogis.git   (your fork; optional)
upstream → https://github.com/lumogis/lumogis.git (canonical upstream; add if you fork)
```

Default working branch: `dev`.

### Daily flow — feature branch → PR → merge

```bash
# 1. Sync dev with the latest upstream
git checkout dev
git fetch upstream
git pull upstream dev                       # or: git pull origin dev
git push origin dev                         # keep your fork's dev in sync (if needed)

# 2. Branch off dev for the change
git checkout -b feat/short-description

# 3. Code, test, commit (small, focused commits)
git status                                  # what changed?
git diff                                    # unstaged changes
git diff --staged                           # staged changes
git add -p                                  # stage hunks interactively
git commit -m "feat(orchestrator): add web search tool"

# 4. Push the branch to your fork
git push -u origin feat/short-description   # -u sets upstream so future `git push` works

# 5. Open a PR against upstream (or origin) `dev`
gh pr create --base dev --title "feat: web search tool" --body "..."
# or do it in the GitHub UI
```

### Useful inspection commands

```bash
git status                                  # working tree state
git log --oneline -20                       # last 20 commits, one per line
git log --oneline --graph --all -30         # branch graph
git log -p path/to/file                     # full history of a file
git show <sha>                              # show a specific commit
git blame path/to/file                      # who last touched each line
git diff dev...HEAD                         # what your branch adds vs dev
git diff --stat dev...HEAD                  # summary form
```

### Fixing things

```bash
# Discard unstaged changes in a file (DESTRUCTIVE)
git restore path/to/file

# Unstage a file (keep edits)
git restore --staged path/to/file

# Amend the last commit (only if NOT yet pushed, or you'll need --force-with-lease)
git commit --amend                          # edit message and/or include staged changes
git commit --amend --no-edit                # keep message, just add staged changes

# Move the last commit's changes back into the working tree
git reset --soft HEAD~1                     # keeps changes staged
git reset --mixed HEAD~1                    # keeps changes unstaged (default)
git reset --hard HEAD~1                     # DESTRUCTIVE — throws away the changes

# Reorder/squash/fixup commits before pushing
git rebase -i dev                           # interactive rebase against dev
```

### Updating your branch with new dev commits

```bash
git fetch upstream
git checkout feat/short-description
git rebase upstream/dev                     # preferred: linear history
# resolve conflicts, then:
git add <conflicted-files>
git rebase --continue
git push --force-with-lease                 # safe-ish force push after rebase
```

Or if you prefer merges over rebase:

```bash
git merge upstream/dev
git push
```

### Stashing work-in-progress

```bash
git stash                                   # shelve unstaged + staged changes
git stash -u                                # also include untracked files
git stash list
git stash pop                               # re-apply most recent stash and drop it
git stash apply stash@{1}                   # apply a specific one without dropping
git stash drop stash@{1}
```

### Tags & releases

```bash
git tag -a v0.5.0 -m "release: v0.5.0"
git push origin v0.5.0
git tag -d v0.5.0                           # delete locally
git push origin :refs/tags/v0.5.0           # delete on remote
```

### Recovering from "oh no"

```bash
git reflog                                  # shows every HEAD movement; almost nothing is truly lost
git reset --hard HEAD@{2}                   # jump back to a previous HEAD position
git fsck --lost-found                       # find dangling commits/blobs
```

---

## 6. End-to-End Examples

### Example A: Edit a Python file in `orchestrator/`, see it run

```bash
# Option 1: dev mode with hot reload (no rebuild)
make dev                                    # foreground; Ctrl-C to stop
# edit orchestrator/<file>.py — uvicorn auto-reloads

# Option 2: production-style rebuild
vim orchestrator/api/routes.py
docker compose up -d --build orchestrator
docker compose logs -f orchestrator         # watch it boot
curl -s http://localhost:8000/health | python3 -m json.tool
```

### Example B: Add a new Python dependency

```bash
echo "httpx==0.27.2" >> orchestrator/requirements.txt
docker compose build --no-cache orchestrator
docker compose up -d orchestrator
docker compose exec orchestrator python -c "import httpx; print(httpx.__version__)"
```

### Example C: Change an env var in `.env`

```bash
vim .env                                    # change e.g. RERANKER_BACKEND=bge
docker compose up -d                        # Compose recreates affected services
docker compose logs -f orchestrator
```

### Example D: Wipe everything and start fresh

```bash
docker compose down -v                      # DESTRUCTIVE — deletes all data volumes
docker compose up -d --build
```

### Example D2: Export and re-import one user's data

Per-user portable archives live under `${USER_EXPORT_DIR}` (defaults to
`/workspace/backups/users` inside the orchestrator container). The
admin endpoints expect a Bearer token from the admin's own JWT.

```bash
# Export self (user or admin):
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{}' \
     http://localhost:8000/api/v1/me/export -o my-export.zip

# Admin exporting on behalf of another user (body field, not query param).
# Unknown target_user_id returns 404 (NOT a silent empty archive):
curl -sS -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"target_user_id": "u_a1b2c3..."}' \
     http://localhost:8000/api/v1/me/export -o alice-export.zip

# List existing archives (admin):
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
     http://localhost:8000/api/v1/admin/user-imports

# Dry-run an import — validates the archive without writing
# (returns 200 with a structured ImportPlan; no rows touched):
curl -sS -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{
           "archive_path": "alice/export_20260418_100000.zip",
           "new_user": {
             "email": "alice2@example.com",
             "password": "twelve-char-min-please",
             "role": "user"
           },
           "dry_run": true
         }' \
     http://localhost:8000/api/v1/admin/user-imports

# Real import — set dry_run=false; mints a fresh user.
# Returns 201 Created with `Location: /api/v1/admin/users/{new_user_id}`.
# Use `-i` to see the headers:
curl -i -sS -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{
           "archive_path": "alice/export_20260418_100000.zip",
           "new_user": {
             "email": "alice2@example.com",
             "password": "twelve-char-min-please",
             "role": "user"
           },
           "dry_run": false
         }' \
     http://localhost:8000/api/v1/admin/user-imports

# The legacy whole-instance NDJSON dump is gone in v1; this returns
# 410 Gone with the new endpoint named in the body:
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
     http://localhost:8000/export
# {"detail":{"error":"deprecated","successor":"POST /api/v1/me/export", ...}}
```

Operator-visible audit rows for the import lifecycle:

| `action_name`               | When                                                          |
| --------------------------- | ------------------------------------------------------------- |
| `__user_import__.refused`   | Pre-write refusal (email_exists, parent UUID collision, archive_too_large, manifest_invalid, …) — no rows touched |
| `__user_import__.started`   | Refusal gates passed; writes about to begin                   |
| `__user_import__.completed` | All writes succeeded                                          |
| `__user_import__.failed`    | Writes started and an unexpected exception interrupted them — partial state may exist; investigate |

See `docs/per-user-export-format.md` for the manifest schema, the
credential-redaction list, the refusal-reason → HTTP-status table, the
audit lifecycle, and the v1 CSRF/Bearer posture, and
`docs/connect-and-verify.md` Step 9c for the full end-to-end runbook.

### Example E: Ship a feature

```bash
git checkout dev && git pull upstream dev
git checkout -b feat/new-thing
# ... edit, test ...
make compose-lint && make compose-test
git add -A
git commit -m "feat(scope): describe the change"
git push -u origin feat/new-thing
gh pr create --base dev --title "feat: new thing" --body "What & why"
```

---

## 7. Troubleshooting

| Symptom                                                | Try                                                                         |
| ------------------------------------------------------ | --------------------------------------------------------------------------- |
| Code change doesn't show up                            | You probably forgot `--build`. Run `docker compose up -d --build <svc>`     |
| Container keeps restarting                             | `docker compose logs <svc>` — read the last 50 lines                        |
| Port already in use (8000, 3080, 6333, …)              | `lsof -i :8000` to find culprit; stop it or change the host port mapping    |
| Healthcheck never goes healthy                         | `docker compose exec <svc> sh` and run the healthcheck command manually     |
| "no space left on device" during build                 | `docker builder prune` then `docker image prune -a`                         |
| Stale Postgres / Mongo schema after a refactor         | `docker compose down -v` then `docker compose up -d` (DESTRUCTIVE)          |
| Compose can't find a service                           | Check `COMPOSE_FILE` in `.env` — overlay may not be enabled                 |
| Volume mount shows empty dir on host                   | Permissions / Docker Desktop file-sharing not granted for that path         |
| `make dev` shows old code                              | The dev override only mounts `./orchestrator` — other services still need `--build` |
| Git push rejected (non-fast-forward)                   | Someone pushed first. `git pull --rebase` then push again                   |
| Rebased and now `git push` is rejected                 | Use `git push --force-with-lease` (safer than `--force`)                    |
| Accidentally committed a secret                        | Rotate the secret immediately, then `git filter-repo` or open a fresh branch |

### Quick diagnostic commands

```bash
docker compose ps                           # are containers up + healthy?
docker compose config | head -50            # is my compose config what I think it is?
docker compose logs --tail 100 <svc>        # what's the service complaining about?
docker compose exec <svc> env | sort        # what env vars did the container actually get?
docker compose exec <svc> cat /etc/hosts    # network sanity check
docker network inspect lumogis_default      # what's on the compose network?
```

---

## 8. Reference: Make targets in this repo

```
make build                  # docker compose up --build --pull always -d
make dev                    # hot-reload dev stack (foreground)
make health                 # curl /health
make logs                   # follow orchestrator logs
make ingest                 # POST /ingest with /data
make compose-lint           # ruff in container
make compose-test           # orchestrator unit tests in container
make compose-test-stack-control
make compose-test-integration
make compose-test-kg
make test-graph-parity
make sync-vendored          # re-vendor models into services/lumogis-graph
make demo-seed | demo-test | demo-ready
```

---

## 9. Premium KG Service (`lumogis-graph`) — How to Run & Test It

The premium build adds the out-of-process knowledge-graph service
`lumogis-graph` (port `8001`, container only — no host port in production).
It owns all FalkorDB writes; Core POSTs `/webhook` events to it and pulls
synchronous context via `/context`. See `services/lumogis-graph/README.md`
and `docker-compose.premium.yml` for the authoritative spec.

### 9.1 What "premium KG" means in this repo

Three pieces are required, layered as compose overlays:

| Overlay                              | What it adds                                              |
| ------------------------------------ | --------------------------------------------------------- |
| `docker-compose.yml`                 | Core stack (orchestrator, qdrant, postgres, ollama, …)    |
| `docker-compose.falkordb.yml`        | FalkorDB graph store (the KG's actual database)           |
| `docker-compose.premium.yml`         | The `lumogis-graph` service itself + Core dep on it       |

And Core must run in **service mode** (not in-process plugin mode) for any
of this to matter:

```
GRAPH_MODE=service
```

### 9.2 One-time `.env` setup for premium KG

Open `.env` and set / add:

```bash
# Activate the three compose files (order matters)
COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml

# Tell Core to call out to the KG service instead of running it in-process
GRAPH_MODE=service
KG_SERVICE_URL=http://lumogis-graph:8001
CAPABILITY_SERVICE_URLS=http://lumogis-graph:8001

# Backend wiring (these have sane defaults in the premium overlay)
GRAPH_BACKEND=falkordb
FALKORDB_URL=redis://falkordb:6379
FALKORDB_GRAPH_NAME=lumogis

# Webhook auth — pick ONE:
#   (a) dev mode: leave the secret blank and rely on the insecure flag
KG_ALLOW_INSECURE_WEBHOOKS=true
#   (b) production: set a real secret and remove the insecure flag
# GRAPH_WEBHOOK_SECRET=<long random hex e.g. `openssl rand -hex 32`>

# Optional admin/MCP gates (leave unset in dev)
# GRAPH_ADMIN_TOKEN=...
# MCP_AUTH_TOKEN=...

# Small machines: cap KG container memory
# KG_MEM_LIMIT=1g
```

### 9.3 Bring the premium KG stack up

From the repo root:

```bash
docker compose up -d --build                 # builds + starts everything in COMPOSE_FILE
docker compose ps                            # confirm `lumogis-graph` is "healthy"
```

The KG service deliberately does **not** publish a host port. Reach it from
the host through the Core container or via `exec`:

```bash
# Through Core's docker network
docker compose exec orchestrator curl -s http://lumogis-graph:8001/health

# Or shell into the KG container directly
docker compose exec lumogis-graph sh
curl -s http://127.0.0.1:8001/health
```

If you want the host port mapped (e.g. to hit `/mgm` from a browser during
debugging), layer the parity-premium overlay temporarily:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml:docker-compose.parity-premium.yml \
  docker compose up -d
# now http://localhost:8001/mgm is reachable
```

### 9.4 Smoke test — is premium KG actually wired up?

Run these in order. Each step has a clear pass/fail.

```bash
# 1. KG container is healthy
docker compose ps lumogis-graph
# STATUS column should say "healthy"

# 2. KG /health returns 200 with backend info
docker compose exec orchestrator curl -s http://lumogis-graph:8001/health
# Expect JSON with "status":"ok" and FalkorDB info

# 3. Core sees service mode (not inprocess) — do NOT use /graph/health for
#    wiring: it is Postgres quality metrics only (no graph_mode field).
#    With AUTH_ENABLED=true, add: -H "Authorization: Bearer <token>"
curl -sf http://localhost:8000/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('capability_services', d.get('capability_services'))"
docker compose exec -T orchestrator printenv GRAPH_MODE

# 4. Webhook plumbing works end-to-end: ingest a file, check KG ingested it
make ingest                                  # POST /ingest with /data
docker compose logs -f lumogis-graph         # watch webhooks land
# In another terminal:
docker compose exec orchestrator curl -s http://lumogis-graph:8001/graph/health | python3 -m json.tool
# Node/edge counts should grow as ingest runs
```

If `printenv GRAPH_MODE` is not **`service`**, the orchestrator is not in KG
service mode — fix `.env`, then `docker compose up -d` (or
`docker compose restart orchestrator`). The **`/graph/health` JSON does not
include `graph_mode`**; use `GRAPH_MODE` / `capability_services` / logs
(see `docs/connect-and-verify.md` §5c).

### 9.5 KG unit tests (no live stack needed)

Runs against an isolated test image of the KG service. Fast (~seconds).

```bash
make compose-test-kg
```

Under the hood: builds the `test` stage of `services/lumogis-graph/Dockerfile`
into `lumogis-graph:test` (pytest + ruff baked in), then runs
`python -m pytest tests -x -q` inside it with these env defaults:

```
GRAPH_BACKEND=falkordb
KG_ALLOW_INSECURE_WEBHOOKS=true
KG_SCHEDULER_ENABLED=false
LOG_LEVEL=ERROR
```

Run a subset / single test inside the same image:

```bash
make test-kg-image                           # build lumogis-graph:test
docker run --rm \
  -e GRAPH_BACKEND=falkordb \
  -e KG_ALLOW_INSECURE_WEBHOOKS=true \
  -e KG_SCHEDULER_ENABLED=false \
  lumogis-graph:test python -m pytest tests/test_webhook.py -v
```

Local-venv variant (contributors only — needs `services/lumogis-graph/`
deps installed):

```bash
make test-kg
```

### 9.6 KG integration tests (against the live premium stack)

This exercises the Core ↔ KG wire (`/webhook`, `/context`) for real.

```bash
# 1. Bring up the premium stack
docker compose up -d --build

# 2. Run the orchestrator integration suite — graph tests will execute
#    automatically because falkordb is reachable.
make compose-test-integration
```

Notes:
- The Make target sets `COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml`
  for the test container so the graph tests actually run. If you also want
  premium-mode coverage (Core talking to `lumogis-graph` rather than the
  in-process plugin), append the premium overlay too:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml \
docker compose run --rm \
  -v $(pwd)/tests:/integration-tests:ro \
  orchestrator \
  sh -c "pip install -q -r requirements-dev.txt && \
         python -m pytest /integration-tests/integration -v --tb=short \
                          -m 'integration and not slow and not manual'"
```

### 9.7 Parity test — inprocess vs. service must agree

The flagship correctness test for the extraction. Boots Core twice over the
same fixture corpus (`tests/fixtures/ada_lovelace.md`) and asserts the
resulting FalkorDB snapshots are identical. **Slow** (tears down and rebuilds
the whole stack twice) and not part of `test-integration`.

```bash
make test-graph-parity
```

What it does (see `tests/integration/test_graph_parity.py`):

1. Phase A — `GRAPH_MODE=inprocess`:
   ```
   COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.parity.yml
   ```
2. `docker compose down -v` between phases (clean Postgres/FalkorDB).
3. Phase B — `GRAPH_MODE=service`:
   ```
   COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml:docker-compose.parity.yml:docker-compose.parity-premium.yml
   ```
4. Compares `/graph/health` snapshots — fails if they diverge.

This is **destructive** to the local stack (the `down -v` wipes all named
volumes). Don't run it on a machine where you care about local Postgres /
Qdrant / FalkorDB data.

Skipped automatically if `docker` isn't on PATH, so you can still
`pytest -m integration` without Docker.

### 9.8 Quick endpoint reference (KG service, port 8001)

The KG service uses **different auth than Core** for several paths (middleware
open list, `X-Graph-Admin-Token` on `graph_admin_routes`, JWT when
`AUTH_ENABLED=true` on the container). **Canonical matrix:** `docs/kg_reference.md`
§6.4.

Sketch (dev default: `AUTH_ENABLED=false` on KG, `GRAPH_ADMIN_TOKEN` often
empty):

| Method | Path                   | Auth (typical dev) | Auth (locked down) |
| ------ | ---------------------- | -------------------- | -------------------- |
| GET    | `/health`              | open (middleware)    | open                 |
| GET    | `/capabilities`      | open                 | open                 |
| POST   | `/webhook`             | `GRAPH_WEBHOOK_SECRET` or insecure-dev | same §5.4 matrix |
| POST   | `/context`             | same as `/webhook`   | same                 |
| POST   | `/tools/query_graph`   | webhook bearer       | webhook bearer       |
| GET    | `/graph/*` (viz API)   | open                 | JWT if `AUTH_ENABLED` on KG |
| POST   | `/graph/backfill`      | open or JWT + admin token | `auth_middleware` + `_check_admin` |
| GET    | `/graph/health`        | open if no admin token env | JWT + `X-Graph-Admin-Token` when `GRAPH_ADMIN_TOKEN` set |
| GET/POST | `/kg/*`              | GETs open; writes need `X-Graph-Admin-Token` if `GRAPH_ADMIN_TOKEN` set | JWT if `AUTH_ENABLED` on KG; writes + header |
| GET    | `/mgm`                 | open                 | JWT **admin role**   |
| —      | `/mcp/*`               | `MCP_AUTH_TOKEN`     | same                 |

Hit them with `docker compose exec orchestrator curl -s http://lumogis-graph:8001/<path>`
when host ports are not exposed.

### 9.9 Common premium-KG troubleshooting

| Symptom                                              | Likely cause / fix                                                                 |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `lumogis-graph` keeps restarting                     | `docker compose logs lumogis-graph` — usually FalkorDB not reachable or `GRAPH_BACKEND` wrong |
| `GRAPH_MODE` is not `service` (e.g. `printenv` in the orchestrator) | `.env` missing `GRAPH_MODE=service`; `docker compose up -d` (or `restart orchestrator`). Do not use `/graph/health` to read mode. |
| Webhooks rejected with 401/403                       | `GRAPH_WEBHOOK_SECRET` mismatch between Core and KG, OR set `KG_ALLOW_INSECURE_WEBHOOKS=true` for dev |
| `/mgm` not reachable from browser                    | Premium overlay deliberately hides the port — layer `docker-compose.parity-premium.yml` for a host port mapping |
| `make test-graph-parity` fails on phase B            | Read the diff JSON it dumps; usually a real projection drift between modes         |
| KG container OOM-killed                              | Lower `KG_MEM_LIMIT=1g` (or higher) in `.env`, then `docker compose up -d lumogis-graph` |
| Edits to `services/lumogis-graph/` don't show up     | You forgot `--build`. Run `docker compose up -d --build lumogis-graph`             |
| Vendored `models/webhook.py` drift CI failure        | Ran `make sync-vendored` after editing `orchestrator/models/webhook.py`? Commit both files together |

### 9.10 Premium-KG "I changed X, run Y" lookup

| Changed                                                       | Command                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Python in `services/lumogis-graph/`                           | `docker compose up -d --build lumogis-graph`                             |
| `services/lumogis-graph/requirements.txt` or its `Dockerfile` | `docker compose build --no-cache lumogis-graph && docker compose up -d`  |
| `orchestrator/models/webhook.py` (the wire contract)          | `make sync-vendored && docker compose up -d --build orchestrator lumogis-graph` |
| `.env` (`GRAPH_*`, `KG_*`, `FALKORDB_*`)                      | `docker compose up -d` (Compose recreates affected services)             |
| `docker-compose.premium.yml`                                  | `docker compose up -d`                                                   |
| Just want to bounce KG                                        | `docker compose restart lumogis-graph`                                   |
| Wipe KG state and re-ingest from scratch                      | `docker compose down -v` then `docker compose up -d --build` (DESTRUCTIVE) |

---

## 10. Golden Rules

1. **Always run compose commands from the repo root** — `.env` and the
   compose files are resolved relative to `cwd`.
2. **`-d` for daily dev, foreground for debugging boot issues.**
3. **`--build` whenever you change anything inside an image's build context**
   (Python source, requirements, Dockerfile, copied configs).
4. **`down -v` is destructive.** It deletes Postgres, Mongo, Qdrant, Ollama
   models, and the HuggingFace cache. Don't run it casually.
5. **Branch off `dev`, PR back to `dev`.** Never commit straight to `dev` /
   `main` on the upstream remote.
6. **Small, focused commits** with messages like
   `feat(scope): summary` / `fix(scope): summary` / `docs: summary`.
7. **Don't `git push --force` to a shared branch.** Use
   `--force-with-lease` on your own feature branches only.
