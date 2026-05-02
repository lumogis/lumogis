# Connect Everything & Verify — End-to-End Runbook

> Status: Active  
> Last reviewed: 2026-05-02  
> Verified against commit: 98f02b1  
> Owner: Docs Librarian

A linear, copy-paste runbook for standing up the **full self-hosted stack** (Core, Lumogis Web + Caddy, optional LibreChat, optional **lumogis-graph** + FalkorDB) and proving every link works. Follow top-to-bottom; each step has a clear pass/fail check before you move on.

> Companion to `docs/dev-cheatsheet.md` (reference) and
> `docs/kg_operations_guide.md` (concepts). Run all `docker compose`
> commands from the repo root.

---

## What you're about to wire up

```
                              ┌──────────────────┐
       Browser ──────────────▶│  Lumogis Web UI  │  same-origin :80 (Caddy)
                              └────────┬─────────┘
                                       │ /api/* + static
                                       ▼
       Browser ──(optional)──▶ LibreChat :3080──▶ /v1/chat/completions
       Browser ──▶  /dashboard ──┐
       Browser ──▶  /graph/viz ──┼──▶┌──────────────────┐
       Browser ──▶  /mcp        ◀┘   │ Core orchestrator │  :8000
                                     └──┬───────┬───────┘
                                        │       │ /webhook + /context
                                ingest  │       ▼
                                        │  ┌──────────────┐
                                        │  │ lumogis-graph│ :8001  (optional KG service)
                                        │  └──────┬───────┘
                                        │         │
                          ┌─────────────┼─────────┼──────────────────┐
                          ▼             ▼         ▼                  ▼
                       Qdrant       Postgres   FalkorDB           Ollama
                        :6333       :5432       :6379              :11434
                       (vectors)   (metadata)   (graph)        (embeddings/LLM)
```

Eight services, three storage backends, two HTTP APIs (Core + KG), one
chat UI, one MCP surface. The runbook below verifies each arrow.

---

## Step 0 — Prerequisites

```bash
docker --version              # 24+
docker compose version        # v2 (the plugin, not docker-compose)
git --version
curl --version
python3 --version             # 3.10+ (only used for `python3 -m json.tool`)
```

Optional but recommended: `jq` for nicer JSON output. The runbook uses
`python3 -m json.tool` so it works without it.

---

## Step 1 — Configure `.env` for the full stack (Core + lumogis-graph service mode)

```bash
cd /path/to/lumogis    # repository root — use your actual clone path
cp -n .env.example .env       # only if you don't already have one
```

Open `.env` and make sure these lines are set:

```bash
# --- Compose layout: Core + FalkorDB + lumogis-graph (docker-compose.premium.yml overlay) ---
COMPOSE_PROJECT_NAME=lumogis
COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml

# --- LLM key (at minimum one provider) ---
ANTHROPIC_API_KEY=sk-ant-...           # or OPENAI_API_KEY=...

# --- Folder Lumogis indexes ---
FILESYSTEM_ROOT=./lumogis-data         # absolute path also fine

# --- LibreChat secrets (rotate for production) ---
JWT_SECRET=change-me-in-production
JWT_REFRESH_SECRET=change-me-in-production
# Open registration on the LibreChat surface. Pinned `false` per the
# family-LAN plan binding decisions D6 ("Default `ALLOW_REGISTRATION=false`
# for LibreChat") and D15 ("family operators provision LibreChat accounts
# manually during the transition"). The MULTI-USER audit ranks open LAN
# registration as P0 (A8). LibreChat is no longer the supported multi-user
# surface (plan §23); the supported path is Core-owned auth via the
# Lumogis Web slice (Step 8a) → /api/v1/auth/*. Both `.env.example` and
# `docker-compose.yml` now ship `false` as the fallback default.
ALLOW_REGISTRATION=false

# --- Backend wiring ---
VECTOR_STORE_BACKEND=qdrant
METADATA_STORE_BACKEND=postgres
EMBEDDER_BACKEND=ollama
GRAPH_BACKEND=falkordb
FALKORDB_URL=redis://falkordb:6379

# --- KG service mode: Core must talk to lumogis-graph over the network ---
GRAPH_MODE=service
KG_SERVICE_URL=http://lumogis-graph:8001
CAPABILITY_SERVICE_URLS=http://lumogis-graph:8001

# --- Webhook auth: dev mode (insecure) ---
KG_ALLOW_INSECURE_WEBHOOKS=true
# For production: set GRAPH_WEBHOOK_SECRET=<openssl rand -hex 32> and remove the line above
```

Drop a sample document into the indexed folder so the ingest test has
something to chew on:

```bash
mkdir -p ./lumogis-data
cat > ./lumogis-data/ada.md <<'EOF'
# Ada Lovelace

Ada Lovelace worked with Charles Babbage on the Analytical Engine at the
University of London. The Analytical Engine project was a collaboration
between Lovelace and Babbage that produced the first algorithm intended
for machine processing.
EOF
```

---

## Step 2 — Build & start the whole stack

```bash
docker compose up -d --build
```

First run pulls Postgres, Mongo, FalkorDB, Ollama, LibreChat images and
builds three custom images (orchestrator, stack-control, lumogis-graph).
Plan for ~5 min on first cold boot, ~30 s afterwards.

> The orchestrator entrypoint applies any pending Postgres migrations from
> `postgres/migrations/*.sql` on every boot and tracks them in a
> `schema_migrations` table. Fresh installs use `postgres/init.sql` and
> existing installs heal forward automatically — there is no manual
> migration step. Confirm with:
>
> ```bash
> docker compose logs orchestrator | grep -E '\[migrations\]'
> docker compose exec -T postgres psql -U lumogis -d lumogis \
>   -c "SELECT filename, applied_at FROM schema_migrations ORDER BY filename;"
> ```

Watch it come up:

```bash
docker compose ps
```

**Pass criterion:** every service shows `running` and (where defined)
`healthy`. Specifically you should see all of:

```
orchestrator    running (healthy)
lumogis-graph   running (healthy)
librechat       running (healthy)
qdrant          running (healthy)
postgres        running (healthy)
mongodb         running (healthy)
falkordb        running (healthy)
ollama          running (healthy)
stack-control   running
```

If any of them aren't healthy after ~3 min:

```bash
docker compose ps                                    # which one is red?
docker compose logs --tail 100 <bad-service>         # why
```

---

## Step 3 — Verify each storage backend is reachable

This proves the bottom row of the diagram.

```bash
# Qdrant (vector store)
curl -sf http://localhost:6333/readyz && echo " ✓ qdrant ready"

# Postgres (metadata)
docker compose exec postgres pg_isready -U lumogis && echo " ✓ postgres ready"

# FalkorDB (graph) — speaks Redis protocol
docker compose exec falkordb redis-cli -p 6379 PING   # → PONG

# MongoDB (LibreChat conversation store)
docker compose exec mongodb mongosh --quiet --eval "db.adminCommand('ping')"

# Ollama (embeddings + local LLM)
docker compose exec ollama ollama list                # lists pulled models
```

**Pass criterion:** every command above succeeds. If Ollama has no
models yet, that's fine — it'll pull on first ingest.

---

## Step 4 — Verify Core (orchestrator) is healthy

```bash
curl -sf http://localhost:8000/health | python3 -m json.tool
```

**Pass criterion:** Command succeeds (**HTTP 200** — `curl -sf` fails on **503**)
and JSON includes **`"postgres_ok": true`**. `/health` does **not** include a
top-level `"status"` field — counts and `postgres_ok` are the signal.

Out-of-process capability services (declared in **`CAPABILITY_SERVICE_URLS`**)
appear under **`capability_services`**:

```bash
curl -sf http://localhost:8000/health \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['capability_services'])"
```

With the **service-mode KG** `.env` from Step 1, expect **`registered`** and
**`healthy`** to be **≥ 1** once `lumogis-graph` is up and Core has probed it.
If both stay **0**, Core never discovered the KG URL — check `.env`,
`docker compose ps`, and orchestrator logs.

```bash
curl -s http://localhost:8000/capabilities | python3 -m json.tool | head -40
```

**Pass criterion:** JSON is Core’s **own** capability manifest (community
tools such as `memory.search`). **`GET /capabilities` never lists remote
services** — Core does not register itself in the capability registry, and
that endpoint exists for symmetric discovery only. **`lumogis-graph`
discovery is verified via `capability_services` on `/health`** (above), not
here.

Optional — richer JSON status including per-service capability health (same
registry as `/health`):

```bash
curl -s http://localhost:8000/ | python3 -m json.tool | head -60
```

---

## Step 5 — Verify the lumogis-graph service is up and Core is wired to it in `service` mode

The KG container has **no host port** by design. Reach it via Core's
network:

```bash
# 5a. KG is alive and both backends respond
docker compose exec orchestrator curl -s http://lumogis-graph:8001/health \
  | python3 -m json.tool
# Expect: {"status": "ok", "version": "...", "falkordb": true,
#          "postgres": true, "pending_webhook_tasks": 0}
```

**Pass criterion for 5a:** **`"falkordb": true`** and **`"postgres": true`**.
If `falkordb` is `false`, FalkorDB is unreachable from the KG container
(check `docker compose ps falkordb` and `FALKORDB_URL=redis://falkordb:6379`
in the KG environment).

```bash
# 5b. KG can see its graph backend (node / edge counts from FalkorDB)
docker compose exec orchestrator curl -s http://lumogis-graph:8001/graph/stats \
  | python3 -m json.tool
# Expect on a fresh stack:
#   {"available": true, "node_count": 0, "edge_count": 0,
#    "top_entities": [], "cooccurrence_threshold": 3}
```

**Pass criterion for 5b:** **`"available": true`**. Counts are 0 until
ingest writes entities — Step 6 verifies they grow.

```bash
# 5c. Core is wired in SERVICE mode and sees lumogis-graph
docker compose exec -T orchestrator printenv GRAPH_MODE KG_SERVICE_URL
curl -sf http://localhost:8000/health \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['capability_services'])"
docker compose logs --tail 200 orchestrator | grep -E 'graph_mode|GRAPH_MODE|capability_registry|lumogis-graph' | tail -20
```

**Pass criterion for 5c:**

- `printenv` prints **`service`** and **`http://lumogis-graph:8001`**.
- `capability_services` reports **`registered ≥ 1`** and **`healthy ≥ 1`**.
- Orchestrator logs include a startup line such as
  `Wired graph mode: service` (from `_wire_graph_mode_handlers`).

> Note: Core's **`/graph/health`** returns Postgres-only **KG quality metrics**
> (duplicate candidates, orphan %, completeness, constraint violations,
> ingestion trend, temporal freshness) — it does **not** include a
> `graph_mode` or `kg_service` block. Don't use it to verify wiring.

If `GRAPH_MODE` is missing or wrong, fix `.env` then:

```bash
docker compose up -d orchestrator         # re-reads .env, recreates if needed
# or, if .env already had it: docker compose restart orchestrator
```

---

## Step 6 — Ingest a document and watch the data flow

This is the end-to-end test: file on disk → extracted → embedded into
Qdrant → metadata into Postgres → entities/edges into FalkorDB via the
KG service.

In **terminal A**, tail the KG container so you see webhooks land:

```bash
docker compose logs -f lumogis-graph
```

In **terminal B**, kick the ingest:

```bash
make ingest
# or, equivalently:
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"path": "/data"}' | python3 -m json.tool
```

You should see in terminal A: `POST /webhook` lines arriving as Core
projects extracted entities into the KG.

When ingest finishes (HTTP 200 in terminal B):

```bash
# 6a. Vectors in Qdrant?
curl -s http://localhost:6333/collections | python3 -m json.tool
# Expect four collections: documents (chunks), entities, signals, conversations.
# Inspect the document chunk collection — points_count should grow with each ingest:
curl -s http://localhost:6333/collections/documents | python3 -m json.tool \
  | grep -E '"(status|points_count|indexed_vectors_count)"'

# 6b. Metadata in Postgres?
# file_index is the ingest source-of-truth (one row per file, with chunk_count
# and graph_projected_at). There is no `documents` table in Postgres — that
# name belongs to the Qdrant collection above. List tables with \dt if unsure.
docker compose exec -T postgres psql -U lumogis -d lumogis -c \
  "SELECT file_path, file_type, chunk_count, ocr_used, graph_projected_at FROM file_index;"

# 6c. Entities/edges in FalkorDB via the KG service?
docker compose exec orchestrator curl -s http://lumogis-graph:8001/graph/stats \
  | python3 -m json.tool
# node_count and edge_count should now be > 0

# 6d. KG quality metrics from Postgres (entity_count > 0, etc.)
curl -s http://localhost:8000/graph/health | python3 -m json.tool
# Postgres-sourced quality view; not a proxy of /graph/stats.
```

**Pass criterion:** all four show populated state. If 6c is zero but
6a/6b are populated, the Core→KG webhook path is broken — see §10
troubleshooting.

---

## Step 7 — Verify the graph search/query endpoints

These are what chat and the visualization call under the hood. In **SERVICE**
mode the **in-process graph plugin does not register** its router on Core,
so the JSON graph APIs and **`/graph/viz`** are served from **`lumogis-graph`**
(inside Docker: `http://lumogis-graph:8001/...`). Core still serves
**`/graph/health`**, **admin/operator** routes under **`/graph/mgm`**, **`/kg/*`**, etc.
(see `docs/kg_reference.md` §6.0 for `AUTH_ENABLED` and admin gating). With
**`AUTH_ENABLED=true`**, pass a **Bearer JWT** to Core graph read APIs when you
are not in a browser session; direct curls to the KG service from the host
require either **publishing port 8001** or `docker compose exec` from a container
on the same network.

```bash
# Search for an entity by substring
docker compose exec -T orchestrator \
  curl -s "http://lumogis-graph:8001/graph/search?q=Ada" | python3 -m json.tool

# Stats summary (node/edge counts + top entities)
docker compose exec -T orchestrator \
  curl -s "http://lumogis-graph:8001/graph/stats" | python3 -m json.tool

# Ego network around a known entity (use a name returned by /graph/search)
docker compose exec -T orchestrator \
  curl -s "http://lumogis-graph:8001/graph/ego?entity=Ada%20Lovelace&depth=1" \
  | python3 -m json.tool

# Path between two entities — note the params are `from_entity` / `to_entity`
docker compose exec -T orchestrator \
  curl -s "http://lumogis-graph:8001/graph/path?from_entity=Ada%20Lovelace&to_entity=Charles%20Babbage&max_depth=4" \
  | python3 -m json.tool
```

**Pass criterion:** `/graph/search?q=Ada` returns at least one node;
`/graph/ego` returns its 1-hop neighbours; `/graph/path` returns
`"path_found": true` with `nodes` and `edges` populated.

> The browser-side `/graph/viz` page on Core (`http://localhost:8000/graph/viz`)
> proxies these same endpoints transparently to `lumogis-graph` for you —
> the direct `lumogis-graph:8001` calls above bypass that proxy for
> verification only.

---

## Step 8 — Verify the browser surfaces

Open each in a browser. These prove the UI side of the stack.

| URL                                       | What you should see                                                                                  |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| http://localhost:8000/web/                | **Lumogis Web (first slice)** — login → email/role → `/signals` demo → logout. Supported auth path.  |
| http://localhost:8000/dashboard           | Lumogis admin dashboard (settings, ingest controls, KG status).                                      |
| http://localhost:8000/graph/viz           | **Inprocess** graph viz on Core. In **service** mode use `lumogis-graph:8001/graph/viz` (or publish **8001**) — see `docs/kg_operations_guide.md` §3.              |
| http://localhost:8000/graph/mgm           | KG management UI (review queue, dedup, stop-list). **`require_admin`** when `AUTH_ENABLED=true`.   |
| http://localhost:6333/dashboard           | Qdrant's built-in collection browser.                                                                |
| http://localhost:3080  *(legacy)*         | LibreChat UI. Sign in with a pre-existing account; **do not enable open registration here** (see §1). |

**Pass criterion:** every URL above resolves to its expected page, and
`/graph/viz` shows nodes and edges matching what step 6 reported. The
LibreChat row is **legacy/transitional** — LibreChat is no longer the
supported multi-user surface (see family-LAN plan §23); the row stays
in this runbook only so existing single-user setups can confirm the
container is healthy until they migrate to the `/web/` slice.

### Step 8a — Lumogis Web first slice (browser-driven `/api/v1/auth/*` proof)

`/web/` is the **first Lumogis Web slice** called out in the family-LAN
plan §24 — a single-file SPA that consumes `/api/v1/auth/*` directly.
It exists to prove that the Phase 1–3.1 auth foundation is usable from a
real browser before the larger Lumogis Web app is built. Four success
criteria, all driven from the page:

1. **User can log in** — POST `/api/v1/auth/login` with email + password.
2. **Sees current email + role** — GET `/api/v1/auth/me` populates the header.
3. **One authenticated endpoint succeeds** — GET `/signals?limit=5` runs
   with the bearer (proves the bearer flow + Phase 3 per-user isolation).
4. **Logout works cleanly** — POST `/api/v1/auth/logout` clears the
   server-side `refresh_token_jti` AND the `lumogis_refresh` cookie.

**Two run modes, both verified:**

- `AUTH_ENABLED=false` (single-user dev, default): the page bypasses
  login entirely — `/me` returns the synthesised `dev@local.lan / admin`
  user, `/signals` is reachable without a bearer, and the logout button
  is disabled. Useful for proving the wiring without standing up a
  family-LAN deployment.
- `AUTH_ENABLED=true` (family-LAN): the page enforces the full bearer
  flow. Sign in with the bootstrap admin (or any user created via
  `/admin/users`), watch the `lumogis_refresh` cookie appear in DevTools
  → Application → Cookies, hit Logout, watch it expire.

The token is stored in `sessionStorage` (cleared on tab close, never
persisted to disk) so a closed tab is effectively logged out client-side
even before the cookie expires. This slice is intentionally minimal —
no router, no build step, no framework — so the browser-side surface
matches exactly what `routes/auth.py` exposes.

---

## Step 9 — End-to-end chat test (the real proof)

The whole point of the stack: ask a question in LibreChat, get an answer
that's grounded in the document you ingested **and** uses the graph for
context.

1. Browse to http://localhost:3080 and sign in.
2. In the model picker, choose your configured model (Anthropic / OpenAI
   / Ollama — whichever you put a key in for).
3. Ask: **"Who did Ada Lovelace work with?"**

**Pass criterion:** the response mentions *Charles Babbage* and ideally
cites the file you ingested. That confirms:

- LibreChat → Core (via `/v1/chat/completions`) ✓
- Core → vector + graph retrieval ✓
- Core → KG service `/context` ✓ (synchronous context lookup)
- Core → LLM provider ✓
- Citations stitched back into the reply ✓

To see exactly what happened under the hood:

```bash
docker compose logs --tail 200 orchestrator    | grep -iE 'context|graph|chat'
docker compose logs --tail 200 lumogis-graph   | grep -iE 'context|webhook'
```

You should see a `POST /context` arriving at `lumogis-graph` during
the chat turn.

Or test the API directly without the UI:

```bash
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Who did Ada Lovelace work with?"}' \
  | python3 -m json.tool
```

---

## Step 9b — Sharing across the household (publish / unpublish)

By default everything you ingest is **personal** — only you (the
authenticated user) can see it. Lumogis adds a `scope` dimension to
every memory row:

| scope      | who can see it                                              | how it gets there                                       |
| ---------- | ----------------------------------------------------------- | ------------------------------------------------------- |
| `personal` | only the row owner (you)                                    | every default write — ingest, chat, signals, dedup      |
| `shared`   | everyone in the household                                   | you explicitly publish via the API below                |
| `system`   | everyone in the household; system-managed                   | only system writers (signal monitor, dedup promotion)   |

Publishing creates a **separate projection row** linked to the
personal source via `published_from`. Your personal row is never
mutated and never deleted by share/unshare. Unpublish removes only
the projection.

The v1 API exposes 12 routes — six publishable resources × {publish,
unpublish}:

```
POST   /api/v1/{notes|audio_memos|sessions|files|entities|signals}/{id}/publish
DELETE /api/v1/{notes|audio_memos|sessions|files|entities|signals}/{id}/publish
```

Body: `{"scope": "shared"}`. **`scope=system` is rejected with 400**
— system-scoped rows are produced exclusively by system-owned
writers.

### Round-trip a shared note

```bash
# Capture a personal note (default scope = personal)
NOTE_ID=$(curl -s -X POST http://localhost:8000/notes \
  -H 'Content-Type: application/json' \
  -d '{"text": "household menu plan"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["note_id"])')

# Publish to the household
curl -s -X POST "http://localhost:8000/api/v1/notes/${NOTE_ID}/publish" \
  -H 'Content-Type: application/json' \
  -d '{"scope": "shared"}'
# → {"resource":"notes","scope":"shared","note_id":"<projection-uuid>","text":"household menu plan"}

# A second user (other household member) lists notes — they see it
curl -s -H "Authorization: Bearer <bob_jwt>" http://localhost:8000/notes
# → includes the shared projection alongside Bob's own personal notes

# Unpublish — projection disappears, your personal source is intact
curl -s -X DELETE "http://localhost:8000/api/v1/notes/${NOTE_ID}/publish" -w '%{http_code}\n'
# → 204
```

### What you'll see in the dashboard

The **Signals** and **Entities** tables now include a small `Scope`
column with three colour-coded badges:

* grey `PERSONAL` — owner-only
* blue `SHARED`  — household-visible (a row you or a household member published)
* amber `SYSTEM` — produced by a system writer (e.g. signal monitor)

There is no publish/unpublish UI in v1; use the API above.

### One-way migration warning

Migration `013-memory-scopes.sql` adds the `scope` and `published_from`
columns and is applied automatically on the next boot. A backup file
produced **after** 013 lands carries those columns and **cannot be
restored into a pre-013 schema** (Postgres rejects the unknown
columns). The reverse direction is fine: a pre-013 dump restores
cleanly into the post-013 schema with `published_from` defaulting to
NULL.

---

## Step 9c — Per-user backup / export / import (Lumogis-Core v1)

Per the per-user backup/export plan
(*(maintainer-local only; not part of the tracked repository)*) and
[ADR 016](decisions/016-per-user-backup-export.md), Lumogis-Core ships
a **per-user** archive flow. Each archive is one user's portable
backup — usable for migration to a different instance, offline backup,
or family-LAN account hand-off. There is no whole-instance dump in v1.

### What is included / excluded

| Included (per-user, scope ∈ {personal, shared})              | Excluded (always)                                        |
| ------------------------------------------------------------ | -------------------------------------------------------- |
| Owner's rows from `notes`, `file_index`, `entities`, `sessions`, `audio_memos`, `signals`, `review_queue`, `action_log`, `audit_log` (scope filtered) | Other users' rows (filtered out by `authored_by_filter`) |
| Owner's rows from `entity_relations`, `sources`, `relevance_profiles`, `review_decisions`, `connector_permissions`, `routine_do_tracking`, `routines`, `feedback_log`, `edge_scores`, `dedup_candidates`, `deduplication_runs`, `known_distinct_entity_pairs`, `constraint_violations` (user_id filtered) | `app_settings`, `kg_settings` (global / system)             |
| Per-user Qdrant points (`documents`, `conversations`, `entities`, `signals`) filtered by payload `user_id` | Cross-user Qdrant points                                 |
| FalkorDB **nodes** with `user_id = me` and intra-user edges  | Cross-user edges (counted in manifest, not exported)     |
| `users/{user_id}.json` with credential-shaped columns **redacted** to `null` | `password_hash`, `refresh_token_jti`, anything ending in `_secret`, `_token`, `_credential`, `_jti`, `_hash` |

The redaction list lives in `services/user_export.py:_REDACTED_FIELD_SUFFIXES`.

### Routes

| Verb + path                                              | Auth                       | Purpose                                                       |
| -------------------------------------------------------- | -------------------------- | ------------------------------------------------------------- |
| `POST /api/v1/me/export`                                 | Bearer (any role)          | Self-export. Body `{}` for self; admins may pass `{"target_user_id": "..."}` to export on behalf of another user. |
| `GET  /api/v1/me/data-inventory`                         | Bearer (any role)          | Read-only per-table row counts ("preview before export").     |
| `POST /api/v1/admin/user-imports`                        | Bearer (admin)             | Import an archive into a fresh account. `dry_run` is supported. |
| `GET  /api/v1/admin/user-imports`                        | Bearer (admin)             | Inventory archives under `${USER_EXPORT_DIR}/*/`.             |
| `GET  /export`                                           | Bearer (admin)             | **`410 Gone`** — deprecated NDJSON dump. Body points at `POST /api/v1/me/export`. |

> **Plan deviation (intentional, see ADR 016):** the legacy
> `GET /export` returns **`410 Gone`** in this build instead of being
> kept byte-for-byte for one release. The successor pointer is in the
> 410 detail body. Slated for removal in a follow-up release once the
> dashboard fully migrates.

### Credentials policy (v1)

The archive **never** carries the source credentials. Importing an
archive **mints a brand-new local account** — the admin must supply a
fresh password in the import body:

```json
{
  "archive_path": "/workspace/backups/users/<source-uuid>/export_YYYYMMDD_HHMMSS.zip",
  "new_user": {
    "email": "alice-imported@home.lan",
    "password": "<a-fresh-strong-password>",
    "role": "user"
  },
  "dry_run": false
}
```

There is no credential transport, no token forwarding, no "import as
the same user". This is binding for v1 and is what makes the
`__user_import__.completed` audit trail meaningful — the destination
operator chose the new password.

### HTTP semantics worth pinning

| Situation                                                            | Status                                | Notes                                                                                     |
| -------------------------------------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------- |
| `POST /api/v1/me/export` self                                        | `200 OK` + `application/zip`          | Streamed via `StreamingResponse`; archive also persisted under `${USER_EXPORT_DIR}/<user>/`.|
| `POST /api/v1/me/export` admin-on-behalf, **unknown** `target_user_id` | `404 Not Found`                       | Body: `{"detail": {"error": "user not found", "target_user_id": "..."}}`. Without this gate the export would silently produce an empty archive — operators can't tell that apart from a real but data-empty user. |
| `POST /api/v1/me/export` non-admin trying to export another user     | `403 Forbidden`                       | `"admin role required to export another user"`.                                           |
| `POST /api/v1/admin/user-imports` `dry_run=true` success             | `200 OK` + `ImportPlan` body          | **Non-mutating** — no user row, no inserts. Return body shows `would_succeed`, preconditions, dangling refs, warnings. |
| `POST /api/v1/admin/user-imports` `dry_run=false` success            | **`201 Created` + `Location` header** | `Location: /api/v1/admin/users/{new_user_id}` points at the canonical resource for the freshly-minted account. Body is the `ImportReceipt`. |
| Import refused (precondition failed)                                 | `400 / 403 / 409 / 413`               | See "Refusal contract" below. The body always carries `{"detail": {"refusal_reason": "...", "payload": {...}}}`. |

### Refusal contract (refused vs failed)

The import service distinguishes two lifecycle outcomes that operators
care about for very different reasons:

| Audit event                            | When                                                                 | What it means                                                       |
| -------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `__user_import__.refused`              | Any precondition refusal — happens BEFORE writes commit              | **Clean rollback. No partial state. Safe to retry once fixed.**     |
| `__user_import__.started`              | After all preconditions passed; about to write                       | (lifecycle marker)                                                  |
| `__user_import__.completed`            | Writes committed; receipt returned                                   | Success path.                                                       |
| `__user_import__.failed`               | Uncaught exception raised AFTER writes began                         | **Investigate.** Partial state on the destination is possible.      |

Refusal reasons (`detail.refusal_reason`) and their HTTP status code:

| `refusal_reason`                       | HTTP  | When                                                             |
| -------------------------------------- | ----- | ---------------------------------------------------------------- |
| `archive_too_large`                    | `413` | Whole archive or any single entry exceeds the configured cap     |
| `archive_integrity_failed`             | `400` | Not a valid zip, or file not found                               |
| `archive_unsafe_entry_names`           | `400` | Zip-slip / absolute paths / NUL / `..` / drive prefixes          |
| `manifest_invalid`                     | `400` | Missing `manifest.json`, parse error, or wrong shape             |
| `missing_user_record`                  | `400` | Manifest declares user X but `users/X.json` is absent            |
| `manifest_section_count_mismatch`      | `400` | Declared row count ≠ actual JSON length for some section         |
| `missing_sections`                     | `400` | Manifest declares a section that isn't in the zip                |
| `unsupported_format_version`           | `400` | Manifest `format_version` outside the supported set              |
| `forbidden_path`                       | `403` | `archive_path` is outside `${USER_EXPORT_DIR}` allowlist         |
| `email_exists`                         | `409` | Destination already has a user with that email (incl. race)      |
| `uuid_collision_on_parent_table`       | `409` | One of `entities/sessions/notes/audio_memos/signals/sources/deduplication_runs` IDs already exists on the destination |

Every refusal — whether it fired before or after `__user_import__.started`
in the impl — also writes a dedicated `__user_import__.refused` audit
row. Operators can therefore filter the audit table to "what got
rejected and why" without parsing payload fields.

### CSRF / Bearer posture (v1)

`/api/v1/me/export` and `/api/v1/admin/user-imports` carry
`Depends(require_same_origin)` from `csrf.py`. **Bearer-authenticated
requests intentionally bypass that dep in v1** — they are protected
by the bearer secret (which a CSRF attacker cannot forge from a
cross-origin browser context) and CSRF only matters once a cookie
session ships. The bypass is pinned by
`tests/test_user_export_routes.py::test_export_route_with_bearer_skips_csrf_intentionally`
plus a counterpart unit test that proves `require_same_origin` itself
still 403s on the cookie / no-Bearer path. If this posture changes
(e.g. `cross_device_lumogis_web` lands cookie-session auth), both
tests must change at the same time.

### Verification — full curl flow

> All commands assume `AUTH_ENABLED=true` and that you have a bearer
> token for an **admin** account (`ADMIN_TOKEN`) — see Step 8a for
> how to mint one. Replace `ALICE_TOKEN`, `ALICE_ID`, and the archive
> path placeholders.

```bash
# 1. Self-export — alice exports her own data.
curl -sS -X POST http://localhost:8000/api/v1/me/export \
  -H "Authorization: Bearer ${ALICE_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}' \
  -o /tmp/alice_export.zip \
  -w 'HTTP %{http_code} bytes=%{size_download}\n'
# Expect: HTTP 200 bytes=>0; the archive is also persisted to
#   ${USER_EXPORT_DIR}/${ALICE_ID}/export_YYYYMMDD_HHMMSS.zip
#   inside the orchestrator container.

# 2. Inspect manifest without unzipping the whole thing.
unzip -p /tmp/alice_export.zip manifest.json | python3 -m json.tool | head -30

# 3. List archives currently on disk.
docker compose exec -T orchestrator \
  curl -sS http://localhost:8000/api/v1/admin/user-imports \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" | python3 -m json.tool

# 4. Dry-run import — non-mutating preview.
ARCHIVE='/workspace/backups/users/<source-uuid>/export_YYYYMMDD_HHMMSS.zip'
docker compose exec -T orchestrator \
  curl -sS -X POST http://localhost:8000/api/v1/admin/user-imports \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
        \"archive_path\": \"${ARCHIVE}\",
        \"new_user\": {
          \"email\": \"alice-imported@home.lan\",
          \"password\": \"verylongpassword12\",
          \"role\": \"user\"
        },
        \"dry_run\": true
      }" | python3 -m json.tool
# Expect: would_succeed=true, target_email_available=true,
#         no_parent_pk_collisions=true, missing_sections=[].

# 5. Real import — 201 Created + Location header.
docker compose exec -T orchestrator \
  curl -sS -X POST http://localhost:8000/api/v1/admin/user-imports \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -D /tmp/import_headers.txt \
  -d "{
        \"archive_path\": \"${ARCHIVE}\",
        \"new_user\": {
          \"email\": \"alice-imported@home.lan\",
          \"password\": \"verylongpassword12\",
          \"role\": \"user\"
        },
        \"dry_run\": false
      }" | python3 -m json.tool
grep -i '^location:' /tmp/import_headers.txt
# Expect: HTTP/1.1 201 Created
#         Location: /api/v1/admin/users/<new_user_id>

# 6. Replay the same import — refused with 409 email_exists.
docker compose exec -T orchestrator \
  curl -sS -X POST http://localhost:8000/api/v1/admin/user-imports \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{ ... same body ... }" \
  -w '\nHTTP %{http_code}\n' | python3 -m json.tool
# Expect: 409 with detail.refusal_reason="email_exists".

# 7. Confirm the legacy NDJSON dump is gone.
curl -sS http://localhost:8000/export -w '\nHTTP %{http_code}\n'
# Expect: 410 with detail.successor="POST /api/v1/me/export".

# 8. Audit trail — refused vs completed are now separate events.
docker compose exec -T postgres psql -U lumogis -d lumogis -c \
  "SELECT action_name, COUNT(*) FROM audit_log
    WHERE action_name LIKE '__user_import__.%'
    GROUP BY action_name ORDER BY action_name;"
# Expect rows for: __user_import__.dry_run_requested,
#                  __user_import__.dry_run_validation_passed (or _failed),
#                  __user_import__.started,
#                  __user_import__.completed,
#                  __user_import__.refused (one per refused attempt).
```

**Pass criterion:** every command returns the status / body listed above
and the audit query distinguishes `__user_import__.completed`,
`__user_import__.refused`, and (only when something genuinely broke
post-write) `__user_import__.failed`.

### Operational knobs

| Env var                              | Default                       | What it does                                                |
| ------------------------------------ | ----------------------------- | ----------------------------------------------------------- |
| `BACKUP_DIR`                         | `/workspace/backups`          | Parent dir for both per-user archives and any future global backups. |
| `USER_EXPORT_DIR`                    | `${BACKUP_DIR}/users`         | Archive root + import allowlist. Anything outside here is refused with `forbidden_path`. |
| `USER_EXPORT_KEEP_MIN`               | `3`                           | Minimum number of newest archives kept per user (D8 hybrid retention). |
| `USER_EXPORT_MAX_AGE_DAYS`           | `30`                          | Older-than-this archives prune *after* the keep-min floor is satisfied. |
| `USER_EXPORT_MAX_ARCHIVE_BYTES`      | `524288000` (500 MiB)         | Whole-archive cap. Above this an export raises `413`; an import refuses `archive_too_large`. |
| `USER_EXPORT_MAX_PER_ENTRY_BYTES`    | `104857600` (100 MiB)         | Single-entry cap (zip-bomb defence).                        |

---

## Step 9d — Per-user MCP tokens (`lmcp_…`)

Per ADR `mcp_token_user_map` (and the matching plan
*(maintainer-local only; not part of the tracked repository)*), the `/mcp/*` surface is
gated per-user. External MCP clients (Claude Desktop, Cursor,
Thunderbolt, …) authenticate with **per-user opaque bearer tokens**
of the form `lmcp_<45 base32 lowercase chars>` (50 chars total). The
legacy single-secret `MCP_AUTH_TOKEN` is now the **single-user
fallback only** — in `AUTH_ENABLED=true` (family-LAN) it is
fail-closed and the orchestrator emits a one-shot `CRITICAL` log
line pointing at the per-user mint flow.

### Storage shape

| Column         | Notes                                                                                                      |
| -------------- | ---------------------------------------------------------------------------------------------------------- |
| `id`           | UUID hex; primary key.                                                                                     |
| `user_id`      | Owner; rows for a disabled user are cascade-revoked atomically with the disable flip (D7).                 |
| `token_prefix` | First 16 chars of the base32 body — indexed handle for verify(), partial-unique on active rows.            |
| `token_hash`   | SHA-256 hex of the full plaintext bearer (D9). Plaintext is **never** persisted.                           |
| `label`        | Human label shown in the dashboard (1..64 chars).                                                          |
| `scopes`       | `NULL` = unrestricted (v1 default). Empty array = NO ACCESS (reserved). Non-empty = future allowlist.      |
| `last_used_at` | Throttled in-process to one DB write per token per 5 min (D5). Hygiene metadata, not a security counter.   |
| `expires_at`   | Reserved for forward-compat; v1 verifier ignores it and the mint API rejects mint-time `expires_at` (D4).  |
| `revoked_at`   | Set by user revoke, admin revoke, or cascade revoke. Idempotent.                                           |

### Routes

| Verb + path                                                       | Auth          | Purpose                                                       |
| ----------------------------------------------------------------- | ------------- | ------------------------------------------------------------- |
| `POST   /api/v1/me/mcp-tokens`                                    | Bearer (any)  | Mint a fresh `lmcp_…` token. Body: `{"label": "..."}`.  Plaintext returned **once** in the 201 response (`plaintext` field). |
| `GET    /api/v1/me/mcp-tokens?include_revoked=…`                  | Bearer (any)  | List the caller's own tokens. Default excludes revoked rows.  |
| `DELETE /api/v1/me/mcp-tokens/{token_id}`                         | Bearer (any)  | Revoke one of the caller's own tokens. Cross-user attempts return `404`, not `403` (information-leak guard). |
| `GET    /api/v1/admin/users/{user_id}/mcp-tokens?include_revoked=…` | Bearer (admin) | Per-user enumeration (default includes revoked rows for forensics). |
| `DELETE /api/v1/admin/users/{user_id}/mcp-tokens/{token_id}`      | Bearer (admin) | Admin revokes another user's token. Path `user_id` must match the row's owner — mismatch returns `404`. |

The mint request is `extra="forbid"` (D4 / D16): unknown fields
including `expires_at` are rejected with `422`, not silently dropped.

### Audit events (table: `audit_log`)

Every lifecycle event writes one row:

* `__mcp_token__.minted`           — user minted a token.
* `__mcp_token__.revoked`          — user revoked one of their own tokens.
* `__mcp_token__.admin_revoked`    — admin revoked another user's token.
* `__mcp_token__.cascade_revoked`  — `set_disabled(disabled=True)` flipped the user; one row per affected token, attributed to the acting admin (D14).

### Verification — full curl flow

> Assumes `AUTH_ENABLED=true` and that `ALICE_TOKEN` is alice's
> short-lived access JWT (mint via Step 8a). Replace the `lmcp_…`
> placeholder with the real plaintext returned by the mint call.

```bash
# 1. Mint a token (plaintext returned exactly once).
MINT=$(curl -sS -X POST http://localhost:8000/api/v1/me/mcp-tokens \
  -H "Authorization: Bearer ${ALICE_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"label": "claude-desktop"}')
echo "$MINT" | python3 -m json.tool
ALICE_LMCP=$(echo "$MINT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["plaintext"])')
echo "Save this — server has only the SHA-256 from now on:"
echo "  $ALICE_LMCP"

# 2. Use it directly against /mcp/* — no JWT needed.
curl -sS http://localhost:8000/capabilities \
  -H "Authorization: Bearer ${ALICE_LMCP}" | python3 -m json.tool | head -20

# 3. List the caller's tokens (response excludes hash + prefix).
curl -sS http://localhost:8000/api/v1/me/mcp-tokens \
  -H "Authorization: Bearer ${ALICE_TOKEN}" | python3 -m json.tool

# 4. Revoke. Idempotent — re-running returns the same 200 + revoked_at.
TOKEN_ID=$(echo "$MINT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"]["id"])')
curl -sS -X DELETE "http://localhost:8000/api/v1/me/mcp-tokens/${TOKEN_ID}" \
  -H "Authorization: Bearer ${ALICE_TOKEN}" -w '\nHTTP %{http_code}\n' | python3 -m json.tool

# 5. The plaintext bearer no longer authenticates.
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000/mcp/ \
  -H "Authorization: Bearer ${ALICE_LMCP}"
# Expect: HTTP 401

# 6. Audit trail.
docker compose exec -T postgres psql -U lumogis -d lumogis -c \
  "SELECT action_name, COUNT(*) FROM audit_log
    WHERE action_name LIKE '__mcp_token__.%'
    GROUP BY action_name ORDER BY action_name;"
```

**Pass criterion:** the mint returns the plaintext exactly once,
the bearer authenticates `/mcp/*` calls until revoke, the revoke
returns 200 (and is idempotent), and the audit_log carries one
`__mcp_token__.minted` and one `__mcp_token__.revoked` row.

### Dashboard (Web → MCP tokens)

`orchestrator/web/index.html` carries a collapsed "MCP tokens" tile
under the signed-in card. Expand it, type a label, click **Mint new
token**, copy the plaintext from the modal (it is shown exactly once
— the modal falls back to an inline rendering on browsers without
`HTMLDialogElement.showModal`). Active and revoked tokens are
listed below; the **Revoke** button calls
`DELETE /api/v1/me/mcp-tokens/{id}`.

---

## Step 9e — CalDAV calendar source (per-user, `AUTH_ENABLED=true`)

Per ADR 018 + plan `caldav_connector_credentials`, CalDAV credentials
live in the encrypted `user_connector_credentials` table under
connector id `caldav` — **not** in process environment. The
deployment-wide `CALENDAR_CALDAV_URL` / `CALENDAR_USERNAME` /
`CALENDAR_PASSWORD` / `CALENDAR_POLL_INTERVAL` env vars are
**legacy single-user only** (`AUTH_ENABLED=false`); under
`AUTH_ENABLED=true` the legacy `__caldav__` APScheduler job refuses
to schedule and emits a single deprecation INFO log on first
`start()` call. The canonical multi-user path is per-user `sources`
rows polled by `feed_monitor`.

### Wire shape (encrypted payload)

```json
{
  "base_url": "https://nextcloud.example.com/remote.php/dav/",
  "username": "alice",
  "password": "<secret>"
}
```

All three fields are required, non-empty strings.
`base_url` MUST be a `http` or `https` URL with a non-empty host
(no leading/trailing whitespace). Validation runs on every read;
malformed payloads log `code=credential_unavailable` and the poll
is skipped.

### Plain-text vs encrypted-payload split

| Lives in `sources` row (plain config) | Lives in `payload` (sealed) |
| --- | --- |
| `id`, `name`, `source_type='caldav'`, `category`, `active`, `poll_interval`, `extraction_method='caldav'`, `user_id` | `base_url`, `username`, `password` |
| `url` is **display-only** for `source_type='caldav'` rows — the adapter ignores it and uses `payload.base_url` instead | — |

Storing `base_url` in the encrypted payload keeps every secret +
secret-adjacent field on the same rotation key. `sources.url` is
populated for parity with other source types (so the dashboard's
"sources list" view doesn't show an empty cell) but is never read by
the CalDAV adapter.

### Step 1 — store the per-user credentials

```bash
# Cookie-auth, same-origin POST/PUT/DELETE go through the
# /api/v1/me/connector-credentials surface. Replace the values below
# with your real CalDAV server / username / password.
curl -fsS -X PUT \
    --cookie cookies.txt \
    -H 'Origin: http://localhost:8000' \
    -H 'Content-Type: application/json' \
    -d '{"payload": {
          "base_url": "https://nextcloud.example.com/remote.php/dav/",
          "username": "alice",
          "password": "REDACTED"
        }}' \
    http://localhost:8000/api/v1/me/connector-credentials/caldav \
  | python3 -m json.tool
```

Expect HTTP 200 with metadata only — `created_by: "self"`,
`updated_by: "self"`, no `payload` / `ciphertext` field.
Verify the row exists and contains no plaintext on disk:

```bash
curl -fsS --cookie cookies.txt \
    http://localhost:8000/api/v1/me/connector-credentials/caldav \
  | python3 -m json.tool
docker compose exec -T postgres psql -U lumogis -d lumogis -c \
  "SELECT user_id, connector, key_version, created_at FROM user_connector_credentials WHERE connector='caldav';"
```

### Step 2 — register the source

```sql
-- Run inside Postgres:
--   docker compose exec -T postgres psql -U lumogis -d lumogis
INSERT INTO sources
  (id, user_id, name, source_type, url,
   category, active, poll_interval, extraction_method)
VALUES
  (gen_random_uuid()::text,
   '<your-user-id>',
   'My Nextcloud calendar',
   'caldav',
   'https://nextcloud.example.com/remote.php/dav/',  -- display only
   'calendar',
   TRUE,
   3600,                                              -- see "poll cadence" below
   'caldav');
```

### Poll cadence (canonical multi-user path)

`sources.poll_interval` is the canonical knob (default 3600 s in
`postgres/init.sql`). Each user's `sources.caldav` rows are picked
up by `feed_monitor` and polled on their per-row cadence.

> **Recommended floor: 1800 s (30 min).** v1 limitation: CalDAV
> events emit `Signal.url=""`, which disables URL-based deduplication
> in `feed_monitor` and collapses the LLM importance-score cache key
> across every event. Each poll therefore re-runs the full LLM
> pipeline for every event in the lookahead window. A higher
> `poll_interval` directly bounds LLM cost. A future signal-contract
> ADR will make CalDAV deduplication first-class; until then,
> operators with paid LLM backends should keep `poll_interval >= 1800`.

`CALENDAR_POLL_INTERVAL` (env) only affects the legacy `__caldav__`
job under `AUTH_ENABLED=false`. `CALENDAR_LOOKAHEAD_HOURS` (env)
remains deployment-wide for both paths in v1.

### Step 3 — verify isolation

```bash
# Alice's row exists, Bob's does not (404).
curl -fsS --cookie alice-cookies.txt \
    http://localhost:8000/api/v1/me/connector-credentials/caldav \
    -o /dev/null -w '%{http_code}\n'   # 200
curl -fsS --cookie bob-cookies.txt \
    http://localhost:8000/api/v1/me/connector-credentials/caldav \
    -o /dev/null -w '%{http_code}\n'   # 404
```

A subsequent `feed_monitor` poll cycle will resolve each user's
credentials independently; the orchestrator's structured logs
include `user_id`, `connector`, and a `code` field
(`connector_not_configured` when the row is missing,
`credential_unavailable` when decrypt or payload validation fails).
The exception object itself is **never** logged on the skip path —
the caldav / requests / urllib3 stack can carry credential URLs in
`repr(exc)`, so the warning carries only the structured fields.

---

## Step 9f — Per-user connector permissions (`ASK` / `DO`)

Connector permissions are **per-user since Lumogis-Core v2026.05**.
Alice flipping `filesystem-mcp` to `DO` no longer flips it for Bob,
and Alice's 15 routine approvals of `send_email` no longer
auto-elevate the action for the rest of the household. Closes audit
finding **A2**.

### Storage shape

* `connector_permissions(user_id, connector, mode)` — `UNIQUE(user_id, connector)`.
* `routine_do_tracking(user_id, connector, action_type, approval_count, edit_count, auto_approved, granted_at)` — `UNIQUE(user_id, connector, action_type)`.
* Both tables ship the migration `016-per-user-connector-permissions.sql`
  that fans existing rows out per real user and sweeps the legacy
  `'default'` placeholder. Empty deployments (no `users` rows yet) are
  left untouched and the bootstrap admin inherits any non-`ASK` rows
  on first user creation via the `db_default_user_remap` hook.

### Lazy default

Connectors **without** an explicit per-user row resolve to the
`_DEFAULT_MODE = 'ASK'` fallback at runtime — no row needs to exist
for a user to be in the safe (read-only) mode. The single-connector
`GET` returns `is_default=true, updated_at=null` when the row is
implicit; the list endpoint fans the lazy default across every
connector that the live capability registry knows about.

### Routes

| Method | Path                                                     | Auth                      | Notes                                                                                                                |
| ------ | -------------------------------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/v1/me/permissions`                                 | `require_user`            | Effective view for the caller across every known connector. Returns `[]` + `Warning: 199` when the registry is down. |
| GET    | `/api/v1/me/permissions/{connector}`                     | `require_user`            | Single-connector effective view. `404` for unknown connector when the registry is reachable.                         |
| PUT    | `/api/v1/me/permissions/{connector}`                     | `require_user` + same-origin | UPSERT the caller's mode. Body: `{"mode":"ASK"}` or `{"mode":"DO"}` (canonical case; lowercase yields `422`).        |
| DELETE | `/api/v1/me/permissions/{connector}`                     | `require_user` + same-origin | Drop the caller's explicit row. Returns the now-default row. Idempotent.                                              |
| GET    | `/api/v1/admin/users/{user_id}/permissions[/{connector}]` | `require_admin`           | Admin on-behalf-of-user view; `404` when the target user does not exist.                                              |
| PUT    | `/api/v1/admin/users/{user_id}/permissions/{connector}`  | `require_admin` + same-origin | Admin UPSERT on behalf of `user_id`. Logs `permission_changed_by_admin`.                                              |
| DELETE | `/api/v1/admin/users/{user_id}/permissions/{connector}`  | `require_admin` + same-origin | Admin drop on behalf of `user_id`. Logs `permission_deleted_by_admin`.                                                |
| GET    | `/api/v1/admin/permissions`                              | `require_admin`           | Cross-user enumeration of every explicit row, sorted by `(user_id, connector)`. Implicit defaults are excluded.       |

### Soft-deprecated legacy surface

The legacy `GET /permissions` and `PUT /permissions/{connector}`
routes still exist this release and now require `require_admin`. Each
response carries:

```http
Deprecation: true
Link: </api/v1/me/permissions/{connector}>; rel="successor-version"
```

The legacy `PUT` writes the **calling admin's own** per-user row (not
a global one — there is no global row anymore). Both legacy routes
emit a single `legacy_*_permissions_used` WARN log per call. They
**will return `410 Gone` in the next minor release** — see
`CHANGELOG.md` under "Deprecations".

### Verification — full curl flow

Assumes Step 9d has minted bearer tokens for `alice@home.lan` and
`bob@home.lan`, and a connector named `filesystem-mcp` is registered.

```bash
ALICE_BEARER=…   # from Step 9d
BOB_BEARER=…

# 1. Both users start at the lazy default — implicit ASK, no row.
curl -fsS -H "Authorization: Bearer $ALICE_BEARER" \
    http://localhost:8000/api/v1/me/permissions/filesystem-mcp
# {"connector":"filesystem-mcp","mode":"ASK","is_default":true,"updated_at":null}

# 2. Alice flips filesystem-mcp to DO.
curl -fsS -X PUT -H "Authorization: Bearer $ALICE_BEARER" \
    -H "Content-Type: application/json" \
    -d '{"mode":"DO"}' \
    http://localhost:8000/api/v1/me/permissions/filesystem-mcp
# {"connector":"filesystem-mcp","mode":"DO","is_default":false,"updated_at":"…"}

# 3. Bob still sees ASK — Alice's flip did NOT leak.
curl -fsS -H "Authorization: Bearer $BOB_BEARER" \
    http://localhost:8000/api/v1/me/permissions/filesystem-mcp
# {"connector":"filesystem-mcp","mode":"ASK","is_default":true,"updated_at":null}

# 4. Alice resets back to default (DELETE is idempotent).
curl -fsS -X DELETE -H "Authorization: Bearer $ALICE_BEARER" \
    http://localhost:8000/api/v1/me/permissions/filesystem-mcp
# {"connector":"filesystem-mcp","mode":"ASK","is_default":true,"updated_at":null}
```

### Multi-worker cache caveat

`orchestrator/permissions.py` keeps an **in-process** `dict` cache
keyed by `(user_id, connector)`. Lumogis-Core ships single-worker
today, so the cache is always coherent. If you run uvicorn with
`--workers > 1` (or run multiple orchestrator replicas behind a
load balancer) a `PUT` served by worker #1 will not invalidate the
cache slot on worker #2 — followers may serve stale modes for up to
the next cache miss. There is no shared cache (no Redis dependency
in core); promote to a shared cache only if you actually run
multi-worker. Tracked as deferred follow-up.

### Disabled-user nuance

Disabling a user (admin → `set_disabled(disabled=True)`) clears that
user's cache slots and revokes all MCP-bearer rows immediately, but
JWT-bearer access tokens already in flight remain valid for up to
`ACCESS_TOKEN_TTL_SECONDS` (default 900s). The routes still consult
`get_connector_mode` per request; a disabled user inside the TTL
window will continue to resolve their own per-user mode until the
JWT expires. Promoting the disabled-check into `get_connector_mode`
is a deferred follow-up.

---

## Step 9g — Per-user LLM provider keys (`AUTH_ENABLED=true`)

Per ADR `llm_provider_keys_per_user_migration`, the six cloud LLM
vendor API keys (Anthropic, OpenAI, xAI, Perplexity, Gemini, Mistral)
live in the encrypted `user_connector_credentials` table under
connector ids `llm_anthropic`, `llm_openai`, `llm_xai`,
`llm_perplexity`, `llm_gemini`, `llm_mistral` — **not** in process
environment, **not** in the global `app_settings` table. Each user's
chat resolves their own row at request time; there is no household
"any user has a key" fallback under auth-on. The legacy
`PUT /settings` `api_keys` body is rejected with `422
legacy_global_api_keys_disabled` when `AUTH_ENABLED=true`.

### Wire shape (encrypted payload)

```json
{
  "api_key": "<vendor-issued secret>"
}
```

Single field, non-empty string. Extra fields and non-string
`api_key` values yield `422 invalid_llm_payload` from the route.

### Routes

| Method | Path                                                                | Auth                          | Notes                                                                                  |
| ------ | ------------------------------------------------------------------- | ----------------------------- | -------------------------------------------------------------------------------------- |
| GET    | `/api/v1/me/connector-credentials/llm_<vendor>`                     | `require_user`                | Metadata-only (`{"present":true,"updated_at":…}` or `404`); never returns plaintext.   |
| PUT    | `/api/v1/me/connector-credentials/llm_<vendor>`                     | `require_user` + same-origin  | Body `{"api_key":"…"}`. Encrypts at rest, fires the cache-invalidation listener.       |
| DELETE | `/api/v1/me/connector-credentials/llm_<vendor>`                     | `require_user` + same-origin  | Idempotent. Subsequent chats for that vendor return `424 connector_not_configured`.    |
| GET    | `/api/v1/admin/users/{user_id}/connector-credentials/llm_<vendor>`  | `require_admin`               | Admin on-behalf-of-user metadata view.                                                 |
| PUT    | `/api/v1/admin/users/{user_id}/connector-credentials/llm_<vendor>`  | `require_admin` + same-origin | Admin write on behalf of user. Audit row records `admin:<admin_user_id>` as actor.     |

### UI

Dashboard → **My LLM keys** tile lists all six vendors with a
present/missing badge and per-vendor PUT / DELETE buttons. Admins
also see **Connector credentials** under each user card on the
Admin → Users page.

### Verification — minimal curl flow

```bash
ALICE_BEARER=…   # from Step 9d

# 1. Alice has no Anthropic key yet — the route returns 404.
curl -i -H "Authorization: Bearer $ALICE_BEARER" \
    http://localhost:8000/api/v1/me/connector-credentials/llm_anthropic
# HTTP/1.1 404 Not Found

# 2. Alice writes her Anthropic key. PUT is encrypt-at-rest;
#    no plaintext is logged or returned.
curl -fsS -X PUT -H "Authorization: Bearer $ALICE_BEARER" \
    -H "Content-Type: application/json" \
    -d '{"api_key":"sk-ant-…"}' \
    http://localhost:8000/api/v1/me/connector-credentials/llm_anthropic
# {"present":true,"updated_at":"…"}

# 3. Subsequent /v1/models for alice now lists every claude alias;
#    bob (without his own key) does not see them in his /v1/models.
curl -fsS -H "Authorization: Bearer $ALICE_BEARER" \
    http://localhost:8000/v1/models | python3 -m json.tool

# 4. Calling /v1/chat/completions for a vendor alice has no key for
#    yields the OpenAI-compatible error envelope:
curl -i -X POST -H "Authorization: Bearer $ALICE_BEARER" \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-5","messages":[{"role":"user","content":"hi"}]}' \
    http://localhost:8000/v1/chat/completions
# HTTP/1.1 424 Failed Dependency
# {"error":{"type":"connector_not_configured","code":"llm_openai", "vendor":"openai", …}}
```

### Migrating from a pre-auth deployment

```bash
docker compose exec orchestrator python -m scripts.migrate_llm_keys_to_per_user \
    --user-id alice --user-id bob \
    --dry-run
# inspect the JSON summary, then re-run without --dry-run.
docker compose exec orchestrator python -m scripts.migrate_llm_keys_to_per_user \
    --user-id alice --user-id bob \
    --delete-legacy
```

The script fans every plaintext `*_API_KEY` row in `app_settings`
out into a per-user encrypted row for every named user, and (with
`--delete-legacy`) removes the plaintext row only after every PUT
for that key succeeds. It runs in a separate process from the live
uvicorn orchestrator — restart the `orchestrator` container if any
named user already had an LLM adapter cached for that vendor (no-op
on greenfield migrations). See the script's module-level docstring
for the full exit-code matrix.

### Background job nuance

Background jobs that need a cloud LLM (signal scoring, weekly
review, session summary, entity extraction) thread their owning
`user_id` into `config.get_llm_provider`. Non-user-scoped defaults
(e.g. household-wide nightly digests) MUST stay on local-only
models — the `_check_background_model_defaults()` boot-time gate in
`config.py` aborts startup if a cloud-vendor model is wired into a
non-user-scoped background job under `AUTH_ENABLED=true`.

---

## Step 10 — (Optional) Connect an external MCP client

Core mounts the MCP server at `/mcp`. Useful for connecting Cursor,
Claude Desktop, or another MCP-aware client to your local Lumogis.

```bash
# Confirm MCP is enabled (fields live on GET / — not on /health)
curl -s http://localhost:8000/ | python3 -m json.tool | grep -E 'mcp_|"status"'
docker compose logs --tail 50 orchestrator | grep -i 'MCP server mounted'
# Expect: "MCP server mounted at /mcp (stateless HTTP, JSON responses)"
```

Client config (Claude Desktop / Cursor) — minimal, no auth:

```json
{
  "mcpServers": {
    "lumogis": {
      "url": "http://localhost:8000/mcp",
      "transport": "http"
    }
  }
}
```

If your install has `AUTH_ENABLED=true` (family-LAN, multi-user),
mint a per-user `lmcp_…` token via Step 9d (or via the dashboard's
"MCP tokens" tile) and add:

```json
"headers": { "Authorization": "Bearer lmcp_<45-base32-chars>" }
```

The legacy single-secret `MCP_AUTH_TOKEN` is the **single-user
fallback only**. With `AUTH_ENABLED=true` it is rejected with `401`
and the orchestrator emits a one-shot `CRITICAL` log line — the
supported path is the per-user `lmcp_…` token above. With
`AUTH_ENABLED=false` (single-user dev), `MCP_AUTH_TOKEN=<secret>`
still works as a shared secret and is sent as
`"headers": { "Authorization": "Bearer <secret>" }`.

After restarting the client, list tools — you should see Lumogis tools
plus the six `graph.*` tools served by `lumogis-graph` (Core proxies
them transparently because of `CAPABILITY_SERVICE_URLS`).

---

## Step 11 — Run the automated test suites against the live stack

See [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) for how these layers fit CI and local development.

Now that the stack is up and proven by hand, run the test matrix to
catch regressions:

```bash
# Core unit tests (in container, no live deps)
make compose-test

# Stack-control unit tests
make compose-test-stack-control

# KG service unit tests (isolated test image, no live deps)
make compose-test-kg

# Integration tests against the running stack (Core ↔ FalkorDB)
make compose-test-integration

# Service-mode integration: Core ↔ lumogis-graph over HTTP
COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml \
docker compose run --rm \
  -v $(pwd)/tests:/integration-tests:ro \
  orchestrator \
  sh -c "pip install -q -r requirements-dev.txt && \
         python -m pytest /integration-tests/integration -v --tb=short \
                          -m 'integration and not slow and not manual'"

# Parity test: prove inprocess and service modes produce identical graphs
# WARNING: destructive — runs `docker compose down -v` between phases.
make test-graph-parity
```

**Pass criterion:** every suite is green. The parity test is the
strongest single signal that the KG service extraction hasn't drifted from
the in-process behaviour.

---

## Troubleshooting — connection-specific failures

| Symptom                                                         | Most likely cause / fix                                                                                       |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Core not wired to KG service (still using in-process plugin)    | `.env` missing `GRAPH_MODE=service`. Verify with `docker compose exec orchestrator printenv GRAPH_MODE`; expect `service`. Fix `.env`, then `docker compose up -d orchestrator` (or `restart orchestrator`). Core's `/graph/health` does **not** report wiring — use the env check + `capability_services` on `/health`. |
| Core can't reach `lumogis-graph` (webhooks fail, capabilities show 0) | `KG_SERVICE_URL` / `CAPABILITY_SERVICE_URLS` wrong or empty. Both must be `http://lumogis-graph:8001` (container DNS, not `localhost`). Fix `.env`, restart Core. |
| `lumogis-graph` logs `401 /webhook`                             | `GRAPH_WEBHOOK_SECRET` mismatch. For dev, set `KG_ALLOW_INSECURE_WEBHOOKS=true` and unset the secret on both. |
| `/health` shows `capability_services.registered: 0` on a stack with `lumogis-graph` | Wrong or missing `CAPABILITY_SERVICE_URLS`, KG unhealthy at Core boot, or Core started before KG: fix `.env`, wait for `lumogis-graph` healthy, recreate or restart orchestrator. Remote tools are **not** merged into `GET /capabilities`; check `capability_services` on `/health` or list tools via MCP at `/mcp`. |
| Ingest 200s but FalkorDB stays empty                            | Webhooks not reaching KG. `docker compose logs -f lumogis-graph` while re-ingesting; look for `POST /webhook`. |
| `/graph/viz` is empty after ingest                              | Either FalkorDB really is empty (see above), or browser cached an old empty render — hard reload.             |
| LibreChat 502s when sending a message                           | Core unhealthy. `docker compose logs orchestrator`; usually missing `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.   |
| LibreChat login page won't load                                 | Mongo unhealthy or `JWT_SECRET` empty. Check `docker compose logs librechat mongodb`.                         |
| LibreChat `unhealthy` or logs `EISDIR` / invalid YAML config  | `config/librechat.yaml` must be a **file**, not a directory (bad bind-mount source). Delete the path, copy `config/librechat.coldstart.yaml` → `config/librechat.yaml`, then `docker compose up -d --force-recreate librechat`. Orchestrator entrypoint removes a directory there on startup before seeding. |
| `lumogis-graph` logs `psycopg2.errors.UndefinedColumn: column "graph_projected_at" of relation "file_index" does not exist` (or same for `entities`, or `relation "sessions" does not exist`) | Postgres schema is pre-migration-003. `postgres/init.sql` only runs on a fresh data volume; the orchestrator entrypoint now applies `postgres/migrations/*.sql` automatically on every boot and tracks them in a `schema_migrations` table. Pull, then `docker compose up -d --build orchestrator`. To replay the pending stamp: `docker compose exec -T orchestrator curl -s -X POST http://lumogis-graph:8001/graph/backfill`. To see what's been applied: `docker compose exec -T postgres psql -U lumogis -d lumogis -c "SELECT filename, applied_at FROM schema_migrations ORDER BY filename"`. |
| `/mcp` returns 404                                              | MCP package not installed in the image, or mount failed at boot. Grep orchestrator logs for `MCP mount failed`.|
| Ollama embeddings hang forever on first ingest                  | Model still pulling. `docker compose exec ollama ollama list`; first pull is several GB.                      |
| Everything healthy but chat answer ignores the graph            | Either no entities yet (re-check §6) or chat is hitting the wrong model. Confirm in LibreChat's debug panel.  |
| `make test-graph-parity` wipes your data                        | It calls `down -v` between phases — that's by design. Don't run it on a stack you care about.                 |

### One-liner: "show me everything that's wrong right now"

```bash
docker compose ps && \
echo '--- /health ---' && curl -s http://localhost:8000/health | python3 -m json.tool && \
echo '--- /graph/health ---' && curl -s http://localhost:8000/graph/health | python3 -m json.tool && \
echo '--- KG /health ---' && docker compose exec -T orchestrator curl -s http://lumogis-graph:8001/health | python3 -m json.tool && \
echo '--- KG /graph/stats ---' && docker compose exec -T orchestrator curl -s http://lumogis-graph:8001/graph/stats | python3 -m json.tool
```

Paste the output anywhere you ask for help — it covers 90% of "is the
stack wired together correctly?" diagnostics in one shot.

---

## What "fully connected" looks like — final checklist

After completing all steps:

- [ ] `docker compose ps` — all services healthy
- [ ] All four storage backends respond (Qdrant, Postgres, FalkorDB, Ollama)
- [ ] `/health` on Core returns HTTP 200 with `"postgres_ok": true` (and `capability_services` counts match lumogis-graph wiring)
- [ ] Core wiring: `GRAPH_MODE=service` in the orchestrator container env, and `/health` → `capability_services.registered ≥ 1` (and `healthy ≥ 1`) for `lumogis-graph`
- [ ] `/health` on the KG service (via `exec`) returns `"falkordb": true` and `"postgres": true`
- [ ] `/graph/stats` on the KG service shows `"available": true` (node/edge counts grow after ingest)
- [ ] `make ingest` populates Qdrant, Postgres, **and** FalkorDB
- [ ] `/graph/viz` renders the ingested entities in a browser
- [ ] LibreChat can answer a question that requires both retrieval and graph context
- [ ] `make compose-test`, `compose-test-kg`, `compose-test-integration` all pass
- [ ] (Optional) External MCP client lists Lumogis + KG tools

If every box is ticked, the stack is fully connected and verified end-to-end.
