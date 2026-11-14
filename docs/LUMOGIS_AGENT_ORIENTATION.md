# Lumogis agent orientation

Concise onboarding for AI assistants and contributors working in the **`lumogis/lumogis`** public tree. Summarises **committed evidence** in the checkout — not a live external backlog.

**Maintainer note:** This file is authored under **`docs/public-export/`** in the private product repo and substituted into the AGPL export by **`scripts/create-upstream-export-tree.sh`**. It is **not** the private maintainer context pack.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date** | **2026-11-12** |
| **Branch / commit** | **`dev`** @ **`d5cad400`** (integration branch at refresh; public **0.11.0** on **`main`** until the next publish) |
| **Evidence consulted** | `README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md` (offline test environment), `CONTRIBUTING-BEGINNERS.md` (export template), `Makefile`, `docs/capabilities.md`, `docs/deployment/quickstart.md` (single-user default, turn on sign-in), `docs/LUMOGIS_REFERENCE_MANUAL.md` §Email + §Actions, `docs/guides/capability-egress-containment.md`, `docs/guides/connector-credentials.md`, `docs/testing/automated-test-strategy.md`, `docs/testing/README.md`, `docs/decisions/192–260`, `tests/integration/test_egress_containment.py`, `.github/workflows/ci.yml`, `scripts/public-export-strip-list.txt`, `scripts/test-env.sh`, `CHANGELOG.md` **[0.11.0]** + **[Unreleased]**, `scripts/debug/README.md` |

---

## What Lumogis is

- **Self-hosted household knowledge base** — data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only**).
- **Core** = FastAPI orchestrator; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin) — search, document-chat, capture, sharing, **email triage** (`/email`), unified document library, and admin.
- **Lumogis Search** = Tauri 2 desktop overlay for memory search against your household server (`clients/lumogis-search/`).
- **Household sharing** — per-user personal/shared scopes; documents and entities can be published to the household with scoped visibility.
- **Knowledge graph** — optional FalkorDB-backed graph service (`services/lumogis-graph/`), **AGPL-3.0-only** and part of this tree; off by default (`GRAPH_MODE=disabled`).
- **Optional LibreChat** profile exists for OpenAI-compatible chat; Lumogis Web is the primary surface.
- **Not** a Lumogis-operated SaaS — operators run their own stack.

Published container images (`ghcr.io/lumogis/lumogis-orchestrator`, `ghcr.io/lumogis/lumogis-web`) are built from **this public repo** — see **ADR 037** (`docs/decisions/037-ghcr-publish-public-repo-only.md`).

---

## Repository map

| Path | Role |
| --- | --- |
| `CONTRIBUTING-BEGINNERS.md` | First-time contributor steps + copy-paste agent prompt |
| `orchestrator/` | Core — APIs, services, plugins, actions, signals |
| `clients/lumogis-web/` | Lumogis Web SPA |
| `clients/lumogis-search/` | Lumogis Search desktop overlay (Tauri 2) |
| `services/lumogis-graph/` | Knowledge-graph service (FalkorDB; runs beside Core in `GRAPH_MODE=service`) |
| `stack-control/` | Compose/stack helper service |
| `postgres/` | DB init and migrations |
| `docker/`, `docker-compose*.yml` | Runtime and optional profiles |
| `docs/decisions/` | ADRs |
| `docs/architecture/` | Architecture notes |
| `docs/capabilities.md` | Feature catalogue |
| `docs/LUMOGIS_REFERENCE_MANUAL.md` | Operator narrative |
| `docs/testing/` | Test strategy + **TEST-COVERAGE-MATRIX-*** (core/web) |
| `scripts/` | Doctor, debug runners, fixtures |
| `docker/test-runner/`, `docker/test-env/`, `scripts/test-env.sh` | Offline test-runner image and throwaway test databases (Docker only) |
| `.github/workflows/` | CI — `ci.yml` (lint and unit tests against live Postgres, Qdrant, FalkorDB and Greenmail service containers on Docker-assigned ports, compose-policy, egress containment and guard, web Docker build, **web-checks** lane, `openapi-check`, doctor and backup integration, security audit), plus `web-e2e.yml`, `search-overlay-build.yml`, `changelog.yml` |

---

## Architecture anchors

- **Five concepts:** Services, Adapters, Plugins, Signals, Actions — see [`ARCHITECTURE.md`](../ARCHITECTURE.md).
- **Ask / Do** for tool and action safety — **ADR 006**.
- **Household scopes** — personal/shared/system memory and document visibility — **ADR 015**, **ADR 155**; every point in the `conversations` collection is built by one helper, `services.memory.conversation_payload()`, so shared notes and audio memos recall like sessions — **ADR 255**.
- **Capture** — Quick Capture inbox (edit, commit to memory) and read-only archive for indexed captures — **ADR 161**.
- **Tool catalog** read-only observation vs execution — `orchestrator/services/unified_tools.py`, **ADR 034**.
- **MCP** = external interoperability layer — `orchestrator/mcp_server.py`, memory read/write tools — **ADR 017**, **ADR 128–135**.
- **Context building** — hybrid entity selection and budgets — **ADR 051**; optional chat auto-RAG — **ADR 059**, **ADR 106**.
- **Conversation history** — browse/continue/delete in Lumogis Web; server-side transcript persistence — **ADR 074**, **ADR 085**, **ADR 156**.
- **Admin** — system status, search/retrieval settings (BGE reranker toggle), read-only feature-flags panel, **safety playground** (dry-run injection probes) — **ADR 074**, **ADR 159**, **ADR 160**, **ADR 168**.
- **Capability security** — invoke contract v1, permission scopes on connector grants, community (untrusted) sandbox for out-of-process capabilities, optional container egress containment overlay — **ADR 169**, **ADR 170**, **ADR 171**, **ADR 172**, **ADR 173**; operator guide: `docs/guides/capability-egress-containment.md`.
- **Email connector (IMAP + SMTP)** — read-only mailbox polling and on-box classification (**ADR 174**); per-user taxonomy and optional label write-back (**ADR 175**, **ADR 176**, **ADR 183**); poll cursor safety (**ADR 177**); optional IMAP egress containment (**ADR 178**) and process-wide outbound ceiling (**ADR 179**); proactive surfacing (**ADR 180**); household SMTP send with Ask/Do (**ADR 185**); reply threading and send UX (**ADR 186**, **ADR 187**); voice dictation in composer fields (**ADR 188**); OAuth mailbox connect (**ADR 190**); sender unsubscribe (**ADR 191**); attachment review at **`/email/attachments`** with per-mailbox filing toggles and paperless overlap consent (**ADR 192**, **ADR 193**); mailbox poll health stamped at IMAP connect with a four-code taxonomy (**ADR 195**); OAuth refresh failures carry the same taxonomy (**ADR 196**); SMTP send opt-in maps connection failures to distinct HTTP codes (**ADR 197**); post-setup send enablement on the mailbox card (**ADR 198**); mailbox hosts are read from the stored source URL without decrypting the credential (**ADR 199**), and a credential whose host or port drifts from that URL is rejected (**ADR 200**); recoverable failed sends listed on Approvals with Save to Drafts (**ADR 202**). Lumogis Web **`/email`** triage, unified **`/documents`**, and recoverable-send recovery — see `docs/capabilities.md` and `docs/LUMOGIS_REFERENCE_MANUAL.md` §Email.
- **Email, later decisions** — email is a core opt-in integration (**ADR 205**); reading a message extracts its text and never renders sender HTML (**ADR 207**, **ADR 208**); local search over stored message text (**ADR 211**); unified read across mailboxes and an on-demand sending address (**ADR 213**, **ADR 214**); mailbox auth method in source health (**ADR 225**); transient OAuth refresh failures back off and honour `Retry-After` (**ADR 228**); IMAP hosts in IDNA A-label form (**ADR 237**).
- **Action proposals** — approved proposals execute via atomic claim with terminal failure on the first attempt (no background drain) — **ADR 201** (amends **ADR 040**).
- **Sources and connectors** — `PATCH /sources/{id}` edits any source and every poller records health (**ADR 215**); RSS/page outbound policy (**ADR 216**); each member creates their own sources (**ADR 217**); connector test-connection and on/off switch (**ADR 218**, **ADR 219**); pause/resume a source (**ADR 226**); a switched-off connector is not advertised as active (**ADR 238**); the "what may Lumogis do" autonomy page (**ADR 220**); which connectors Approvals may switch to DO — write-action connectors and known capability services, never read-only tool connectors or one with a hard-limited action (**ADR 258**).
- **Knowledge graph** — AGPL-3.0-only and published here (**ADR 229**, as built **ADR 239**); **`GRAPH_MODE=service` is the only mode that works on the standard Core image** (`inprocess` needs a Core build that includes `services/lumogis-graph/`); fail-closed service mode (**ADR 035**); entity merges repoint typed edges to the winner (**ADR 234**); every node the writer projects carries `scope`, which the visibility filter behind `/graph/stats`, ego, path and mentions requires (**ADR 260**); orphan GC covers personal nodes and co-occurrence counts are idempotent (**ADR 259**); graph pages load cytoscape and Alpine from same-origin vendored files with SRI (**ADR 256**).
- **Per-user ownership** — chat `read_file` and the filename fallback follow search visibility, `POST /ingest` is an admin-only rescan of the configured library folders, `GET /browse` is admin-only (**ADR 230**).
- **Platform** — one process only: boot refuses `WEB_CONCURRENCY` / `--workers` > 1; Postgres connection pool (**ADR 210**); phased forgot-password (**ADR 209**); privacy-mode hint where cloud models are blocked (**ADR 227**); transient chat status outside the answer text (**ADR 224**); background LLM jobs pick their model from one task table (**ADR 241**); chat session summaries stay on local models (**ADR 250**); Lumogis-owned files live in the workspace and are in the backup snapshot (**ADR 248**).
- **Default install and deployment** — Lumogis Web opens in single-user mode with sign-in off, and the quickstart's "Turn on sign-in" section moves the data to the first admin (**ADR 247**); the orchestrator API is published on `127.0.0.1` only, Caddy is the only trusted proxy, and the orchestrator runs as the checkout owner (**ADR 246**); Caddy closes idle Core connections first (**ADR 245**); `make doctor` checks Caddy's trusted proxy against the compose gateway (**ADR 253**); the LiteLLM overlay is loopback-only (**ADR 240**).
- **Sessions and credentials** — account-state changes end every credential, access and refresh tokens are kept apart (**ADR 243**); refresh rotation re-checks the session inside its transaction (**ADR 252**); per-member Web client state is wiped at sign-out and member switch (**ADR 242**); security audits publish a summary of fixed findings only (**ADR 249**).
- **Test layout** — repo-root `tests/integration/` = HTTP smoke against a running Core (its `api` fixture signs in when the stack has sign-in on); `orchestrator/tests/integration/` = in-process against real backends with membership enforced by a pytest marker hook — **ADR 194**; CI runs those against live service containers, and an unreachable backend fails instead of skipping — **ADR 257**; see `docs/testing/automated-test-strategy.md`.
- **Injection defences** — ingest sanitisation, optional secrets scanning on user config, optional tool-result scanning — see `docs/capabilities.md`.
- **Lumogis Search export** — public AGPL overlay at `clients/lumogis-search/`; installer CI ships in this tree — **ADR 080**, **ADR 082**; open-core boundary — **ADR 081**, and **ADR 229** for the knowledge graph.

For operator deployment: [`docs/deployment/quickstart.md`](deployment/quickstart.md), [`README.md`](../README.md).

---

## Verification commands (contributors)

| Layer | Typical command |
| --- | --- |
| Orchestrator unit | `make test` |
| Offline test environment (Docker only) | `scripts/test-env.sh run --tier 1 -- make test`; tier 2 adds throwaway Postgres, Qdrant and FalkorDB — see `CONTRIBUTING.md` |
| Integration hermeticity (touching `orchestrator/tests/integration/`) | `make check-integration-hermeticity` |
| Lint | `make lint` |
| Compose integration | `make compose-test`, `make compose-test-integration` |
| Compose policy (CI parity) | `make compose-policy-check` — run when changing `docker-compose*.yml`, capability overlays, or egress fixtures |
| Capability egress containment (Docker) | `make egress-containment-test` — asserts allowed hosts reach upstream **un-intercepted** and denied hosts are refused; run when changing the proxy overlay or its allow-list |
| Web | `make web-test`, `make web-lint`, `make web-build`, `make web-e2e-prove`; CI runs lint, unit tests, build and the PWA dist check on every push (**ADR 235**); global CSS classes must be `lumogis-`-prefixed (**ADR 222**) |
| Knowledge graph service | `make compose-test-kg` (Docker; graph service suite, not part of `make test`); `make test-graph-parity` (slow; own compose project, service-mode Rescan) |
| Live ingest lane | `make e2e-ingest-live` (Docker, 15–40 min; seeded stack with sign-in on and `GRAPH_MODE=service`; fails on any skip) |
| Search overlay (Vitest + Rust) | `cd clients/lumogis-search && npm test`; `make search-dev`, `make search-build` |
| OpenAPI CI contract | `make openapi-check` |
| Coverage matrix format | `make coverage-matrix-check` (core/web matrices) |
| ADR numbering (PRs adding an ADR) | `make adr-check` — fails on a new duplicate number in `docs/decisions/` |
| Script exec bits | `make exec-bit-check` — committed shell scripts must be `100755` (**ADR 236**) |
| Fast local debug chain | `make debug`, `make test-list` — see `scripts/debug/README.md` |
| Operator health | `make doctor` |

Full strategy: [`docs/testing/automated-test-strategy.md`](testing/automated-test-strategy.md). Feature→test map: [`docs/testing/README.md`](testing/README.md).

---

## How to work as an agent here

1. **First-time contributors:** start with **`CONTRIBUTING-BEGINNERS.md`** (human steps + fenced paste prompt).
2. Read **`AGENTS.md`** (guardrails) and this file (orientation).
3. Read **`ARCHITECTURE.md`** before structural code changes.
4. Prefer **small, scoped** diffs; match existing style in touched modules.
5. Run tests that match touched paths; paste summaries in PRs.
6. For security-sensitive behaviour, read **`SECURITY.md`** and relevant ADRs before proposing changes.
7. Use **GitHub Issues** for follow-ups.

---

## Related docs (depth)

- [`docs/README.md`](README.md) — documentation index
- [`docs/extending/extending-the-stack.md`](extending/extending-the-stack.md) — plugins and extension
- [`CHANGELOG.md`](../CHANGELOG.md) — shipped product changes (latest tagged: **0.11.0**; the integration branch's **[Unreleased]** section carries the knowledge-graph relicence, sources and connector work, email follow-ups, deployment hardening and the single-user default install through **ADR 260**)
- [`TELEMETRY.md`](../TELEMETRY.md) — telemetry posture
