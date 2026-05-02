# Lumogis Self-Hosted Family Platform — Remediation Plan

**Status:** Draft (planning artefact, not an implementation log). **Implementation status:** Chunks 1–6 through Phase 4 **tools / LLM / notifications / admin diagnostics** household-control facades and Web read-only views are **shipped in-repo** as of 2026-04-26 (notifications: 2026-04-25; admin diagnostics summary: 2026-04-26); this file remains the rolling remediation guide (see [`self-hosted-remediation-consolidation-review.md`](self-hosted-remediation-consolidation-review.md)).
**Created:** 2026-04-25.
**Source audit (preserved verbatim):** [`docs/private/LUMOGIS-ARCHITECTURE-AUDIT.md`](../private/LUMOGIS-ARCHITECTURE-AUDIT.md).
**Companion documents read:** `ARCHITECTURE.md`; ADRs `005`, `010`, `011`, `012`, `017`, `018`, `027`, `028` ([`docs/decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md`](../decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md) — Phase 0–4 outcomes), `DEBT.md`; plans `cross_device_lumogis_web`, `lumogis_graph_service_extraction`, `capability_launchers_and_gateway`, `family_lan_multi_user`, `mcp_token_user_map`, `per_user_connector_credentials`, `credential_scopes_shared_system`; maintainer **follow-up portfolio** *(not part of the tracked repository)*; multi-user audit response `docs/private/MULTI-USER-AUDIT-RESPONSE.md`.
**Authoring notes:**
- This file is owned by humans/agents writing architecture guidance; **finalised ADRs** under `docs/decisions/` are canonical in Git, while skill-managed indexes/plans/reviews/explorations/draft mirrors may exist only on maintainer workspaces *(not part of the tracked repository)* — none of those are edited by this plan.
- Implementation chunks below are intended to be turned into individual `/create-plan` invocations later (the canonical chunk slugs are listed in §6 and §7).

---

## 1. Executive reframing

The architecture audit's findings remain valid as written. What changes here is the **prioritisation lens**.

The audit was scored against an implicit "credible multi-tenant ecosystem platform" target — public ecosystem extensibility, marketplace-grade plugins, third-party connector sandboxing, mTLS by default, headless commercial distribution, hosted multi-tenant posture. That target is **not** the near-term Lumogis product target.

**Near-term target (binding for this plan):**

- **Self-hosted** — runs on hardware the household controls (LAN box, NAS, small server).
- **Local-first** — personal data stays in Postgres / Qdrant / FalkorDB / filesystem on that box; cloud LLM use is opt-in per user.
- **Family / household AI** — multiple humans (admin + members) on **one** Lumogis instance, optionally sharing memory via `personal | shared | system` scopes (ADR 015) and household / instance credential tiers (ADR 027).
- **Multi-user within a trusted household / small private deployment** — 2–10 users; not a public tenant of a hosted SaaS.
- **Extensible over time through clean contracts** — `/api/v1/*`, `CapabilityManifest`, `ToolSpec`, `WebhookEvent`, MCP — but extension is **internal first**, public marketplace later.
- **Able to support optional out-of-process services** — `lumogis-graph` (ADR 011) is the reference shape; future premium / heavy modules follow the same pattern.
- **Future-proofed for premium capabilities** — the *contracts* must keep that door open without dragging the near-term work toward it.
- **Not yet marketplace-grade third-party plugins** — third-party connector sandbox, dynamic signed manifests, full mTLS, hosted multi-tenant tenant isolation, and a public Plugin SDK are explicitly **deferred**.

This reframing is consistent with what the repository already records:

- `ADR-012` ("Family LAN multi-user") closes Phase A of the multi-user audit and explicitly defers Phase C (hosted multi-tenant) — `MULTI-USER-AUDIT-RESPONSE.md` §4 and §7.
- `ADR-027` (credential scopes) explicitly defers external vault / broker integrations to "advanced homelabs".
- `ADR-010` (ecosystem plumbing) gates fully-async migration on "the first multi-user deployment requirement", which is exactly the household-scale target — not a hosted scale-out.
- The maintainer **follow-up portfolio** *(maintainer-local only; not part of the tracked repository)* already marks `FP-004` (Hosted / Phase C multi-tenant) and `FP-045` (Multi-tenant pivot) as low-leverage / deferred.

### 1.1 Target platform decomposition (binding vocabulary)

| Concern | Owner | Why this stays in Core (or ships out-of-process) |
| --- | --- | --- |
| Identity, sessions, JWTs, refresh rotation, admin role | **Lumogis Core** (`auth.py`, `services/users.py`, `routes/auth.py`) | Trust anchor; all clients and capabilities derive identity from here (ADR 012). |
| Per-user connector credentials + household / instance tiers | **Lumogis Core** (`services/connector_credentials.py`, `user_connector_credentials`) | Encryption keys never leave the trusted process (ADR 018, ADR 027). |
| Per-user connector permissions (Ask/Do gates) | **Lumogis Core** (`routes/connector_permissions.py`, `connector_permissions` table) | Single source of permission truth (ADR 024). |
| Audit log + reversibility | **Lumogis Core** (`actions/audit.py`, `audit_log`) | Append-only authoritative record (ADR 019). |
| Batch jobs and routine elevation | **Lumogis Core** (`services/batch_queue.py`, `services/routines.py`) | Durable per-user queue (ADR 025). |
| Tool execution policy + LLM tool loop | **Lumogis Core** (`services/tools.py`, `loop.py`) | The unified-execution work in this plan stays here. |
| First-party household control surface | **Lumogis Web** (`clients/lumogis-web/`) — a *client* | Renders Core-owned data; does not own backend logic. |
| OpenAI-compatible chat surface | **LibreChat** (optional profile) — a *client* | Will not become a multi-user surface (ADR 012 §"LibreChat bridge deferred"). |
| Agent tool surface for outside MCP clients | **MCP server in Core** (`mcp_server.py`) — a *transport* | Read-only community tools today; per-user JWT/`lmcp_…` token resolution (ADR 017). |
| Knowledge Graph (writes, reconcile, quality) | **Optional out-of-process capability** (`services/lumogis-graph/`) — invoked via webhook + `/context` + `query_graph` proxy | Heavy deps + premium boundary (ADR 011). |
| Future heavy / premium modules | **Optional out-of-process capabilities** (HTTP, follow KG pattern) | Same shape — Core owns identity + audit; capability owns workload. |
| Marketplace, signed connector manifests, sandboxed third-party code | **Deferred** | Not required for household-scale; revisit triggers in §3 / Phase 6. |

The *ecosystem-platform* target the audit measured against is not abandoned — it is **deferred** and **left buildable** by the contracts we tighten in Phases 0–3 below.

---

## 2. Revised architecture principles

These principles govern the phasing in §3 and the chunks in §6.

1. **Optimise for self-hosted clarity before public ecosystem extensibility.** A household operator should be able to read `ARCHITECTURE.md` + this plan + ADRs 010–027 and predict where any feature lives, without reading audit prose.
2. **Preserve the five-pillar contributor model.** `services / adapters / plugins / signals / actions` stays the way new contributors map code (ARCHITECTURE.md §"Five concepts"). Cross-cutting concerns introduced by this plan — *unified tool execution*, *catalog view*, *capability proxy* — are **overlays** on the five pillars, not a sixth pillar.
3. **Unify execution before adding more capability surfaces.** Do not introduce more *places where a tool can come from* until the LLM-loop / MCP / capability tool universes meet at one permissioned execution path.
4. **Core owns policy, identity, credentials, audit, and approvals.** No client and no out-of-process capability re-implements any of those. Capabilities and clients **consume** Core-owned data through HTTP contracts.
5. **Clients must stay thin.** Lumogis Web and LibreChat translate UX → Core HTTP calls. They do not query Postgres directly, they do not encode credential payload structure, they do not own permission logic.
6. **Capability services can be out-of-process, but must be invoked through Core.** A capability service may not call Postgres or Qdrant on behalf of users; it talks back to Core through the same surfaces (or, like `lumogis-graph`, owns its own private store and exchanges only typed envelopes — `WebhookEnvelope` / `/context` / `/tools/query_graph`).
7. **Avoid big-bang rewrites.** Every chunk preserves the existing working path while introducing the new one. The unified-execution work generalises the **already-shipped** `register_query_graph_proxy` shape rather than inventing a parallel framework.
8. **Every remediation item must have a test or verification path.** Architecture rules become CI gates (forbidden imports, vendored model byte-compare, manifest fixture validation, catalog consistency). Findings without a test become drift bait.
9. **Reversible by environment, not by branch.** New behaviours land behind env flags or are additive to existing endpoints; nothing in Phase 0–3 requires a flag-day cutover for in-process tools or graph mode.

---

## 3. Revised remediation phases

The audit's §17 phases are reorganised below. **No finding is dropped.** The reprioritised backlog table in §4 maps every audit ID into one of these phases (or to deferred buckets P3 / P4).

### Phase 0 — Preserve the audit and freeze the vocabulary

**Purpose.** Make the audit actionable for household-platform work without losing any finding, and lock the words the next three phases use.

**In scope.**

- Keep `docs/private/LUMOGIS-ARCHITECTURE-AUDIT.md` as the source-of-truth audit (no edits beyond accuracy fixes already applied 2026-04-25).
- Build a **mapping table** from each audit backlog ID (B1–B10) and coupling-failure ID (C1–C12) to a revised priority bucket and a target phase in this plan — see §4 below.
- Define the **canonical vocabulary** (binding for §3–§7 and for any plan that consumes this document):

  | Term | Meaning in this plan | Notes |
  | --- | --- | --- |
  | **Core** | The orchestrator process (`orchestrator/*`) and its persistence (`Postgres`, `Qdrant`, `Ollama`, optional `FalkorDB`). The trust anchor. | Ports / adapters live here; `config.py` is the only constructor. |
  | **Client** | A surface that calls Core over HTTP for a specific UX. **Lumogis Web** is the first-party household client; **LibreChat** is an optional OpenAI-compatible client; **MCP clients** (Claude Desktop, Thunderbolt, …) are agent clients. | Clients **own UX** and **never own backend logic**. |
  | **Connector** | A canonical id (`ntfy`, `caldav`, `llm_openai`, …) declared in `connectors/registry.py`; backed by **encrypted per-user credentials** in `user_connector_credentials` (ADR 018) and resolved via `services/connector_credentials.resolve_runtime_credential` across user / household / system tiers (ADR 027). | A connector represents *which secret material applies*, not *what action the user may take*. |
  | **Capability** | An out-of-process feature shipped in its own container that publishes a `CapabilityManifest` (`models/capability.py`) at `GET /capabilities` and is discovered by `services/capability_registry.py`. `lumogis-graph` is the reference (ADR 011). | A capability declares **tools**, optional **`ui_links`** (capability launcher metadata), and may expose its own **MCP** surface internally. |
  | **Plugin** | An **in-process** Python package under `orchestrator/plugins/<name>/` discovered at boot by `plugins/__init__.py:load_plugins`. Lives in the same Python runtime as Core; allowed imports are spelled out in `ARCHITECTURE.md` §"Plugin system" and ADR 005. | Plugins are *trusted* (in-process). Untrusted third-party plugin code is deferred (Phase 6). |
  | **Action** | A registered, audited, side-effecting operation under `orchestrator/actions/` (registry / executor / audit / reversibility). Subject to the **Ask/Do** safety model (ADR 006). | Actions answer *whether* something is allowed and *whether* it can be undone. |
  | **Tool** | A `ToolSpec` (`models/tool_spec.py`) the LLM tool loop can call. May be backed by an in-Core helper, a plugin handler, an action, an MCP wrapper, or — once Phase 3 ships — a capability HTTP endpoint via the proxy pattern. | A tool answers *what the LLM may invoke*. |
  | **Signal** | A monitored external event under `orchestrator/signals/` (`SignalSource.poll`), scored and persisted by `services/signal_processor.py` and surfaced to plugins through `Event.SIGNAL_RECEIVED`. | Signals are *inputs*; they do not execute work themselves. |
  | **Routine** | A scheduled action stream (`services/routines.py`, APScheduler-driven) that elevates from `ask` to `do` after `ROUTINE_ELEVATION_THRESHOLD` clean approvals (ADR 006). | Per-user routine state lives in Postgres; APScheduler ids are scoped by `user_id`. |
  | **MCP surface** | The Core-mounted MCP transport at `/mcp/` (`mcp_server.py`), authenticated with per-user `lmcp_…` tokens or Lumogis JWTs (ADR 017). Stateless HTTP, JSON-only, currently exposes 5 read-only community tools. | MCP is a **transport** for tools; it is **not** the tool registry. A future stateful MCP (e.g., long-running KG queries) belongs in a capability service per ADR 010. |

- **Five-pillar reaffirmation.** The contributor map in `ARCHITECTURE.md` (`services / adapters / plugins / signals / actions`) is preserved. *Tool catalog* and *execution router* introduced later are **cross-cutting**, not a new pillar.
- **Identify contradictory or misleading docs / log terms.** Bring the audit's terminology mismatches into a single fix list:

  | Where | Current wording | Why it misleads | Proposed fix |
  | --- | --- | --- | --- |
  | `ARCHITECTURE.md` line 335 | "Plugins must **never** import from … `config.py`." | Contradicted by `orchestrator/plugins/graph/__init__.py` line 21 (`import config`). | Replace with explicit **allow-list**: `ports/`, `models/`, `events.py`, `hooks.py`, and the named `config` factories the plugin actually needs (cross-reference ADR 005 + a future plugin-boundary addendum). |
  | `orchestrator/routes/connector_permissions.py` (log keys + `Warning` header) | Was "capability registry" but `_known_connectors` reads `actions.registry.list_actions`. | Operators could think the OOP `CapabilityRegistry` failed; the real subsystem is the **action registry**. | **Done (Chunk 1):** `action_registry_unavailable_at_permission_*` log keys, `_ACTION_REGISTRY_UNAVAILABLE_HEADER`, header text `action registry unavailable; connector unvalidated`. |
  | `MCP_DEFAULT_USER_ID` env + `_DEFAULT_USER_ID = "default"` in `mcp_server.py` | Implies legacy single-user fallback is the default path. | After ADR 017 shipped, the per-user `lmcp_…` token path is canonical; the default is a transition fallback only. | Document the precedence chain (`Authorization: Bearer <jwt>` → `Authorization: Bearer lmcp_…` → `MCP_DEFAULT_USER_ID`) once in ARCHITECTURE.md or a maintainers note; don't rename the env (operator-facing surface). |
  | Audit §5 row C11 / §7 row 11 (vendored Core models) | Originally said only `webhook.py`. | Already corrected 2026-04-25 to mention both `webhook.py` and `capability.py`. | No further action; the Makefile and tests already cover both. |
  | Architecture wording vs ADR 005 | ADR 005 says "Core services never import plugin code" (true; verified by `rg 'from plugins' orchestrator/services` returning empty); `ARCHITECTURE.md` overstates plugin import purity. | The two correct halves of the boundary are not framed together. | The plugin-boundary addendum mentioned above resolves both at once. |

**Out of scope.** No runtime change. No file moves. No code deletes. **Docs + non-behavioural terminology cleanup only** — Phase 0 explicitly includes the log-key / advisory-header rename listed in the §3 Phase 0 vocabulary table (e.g. `capability_registry_unavailable_at_permission_validation` → `action_registry_unavailable_at_permission_validation`), which is a string rename with test-assertion updates and **no** runtime-behaviour change. Anything that would alter request handling, persistence, or tool execution belongs to Phase 1 or later.

**Exit criteria.**

1. No audit finding is dropped — every B/C ID has a row in the §4 table.
2. Every audit backlog item has a revised priority bucket (P0–P4) and a target phase.
3. The terminology contradictions list above exists in this document and identifies a fix path for each row.
4. `ARCHITECTURE.md`'s contradictory plugin-import sentence has either been replaced or has a tracked follow-up plan slug (no further drift).

---

### Phase 1 — Boundary hygiene and architecture tests

**Purpose.** Stop further architectural drift before changing runtime behaviour. Make the structure-vs-story gap visible to CI so refactors do not silently regress.

**In scope.**

1. **Fix `routes/signals.py::_detect_source` adapter coupling** (audit C2 / B2). Move RSS / page detection into a service helper that uses ports or a `config` factory; remove the two `from adapters.…` imports inside `_detect_source` (lines 451 and 458). Keep the route's behaviour byte-identical.
2. **Add an import-layer test** at `orchestrator/tests/test_routes_no_adapter_imports.py` (or equivalent slug) that scans `orchestrator/routes/` and fails on any `from adapters` / `import adapters` import. This is the long-missing CI gate the audit calls out (B2, audit §12 row 20).
3. **Vendored model drift check** (audit C11 / B7). The Make target `make sync-vendored` already loops over **both** `webhook.py` and `capability.py` (Makefile lines 98–110); add a CI step that runs `make sync-vendored` and fails if `git diff --name-only services/lumogis-graph/models/` is non-empty. Test slug suggestion: `tests/integration/test_vendored_models_in_sync.py` or a Make recipe gate.
4. **Capability manifest meta-validation in CI** (audit C7 / B3). Add a `tests/test_capability_manifest_validation.py` fixture that loads a known-bad manifest and asserts the registry rejects it (today the registry only validates Pydantic shape, with a deliberate non-goal comment at `services/capability_registry.py` lines 51–56). Optional `jsonschema` validation in production may stay a follow-up; the **CI fixture** is the immediate gate.
5. **Plugin import-boundary clarification** (audit C9 / B5 doc-only slice). Update `ARCHITECTURE.md` (line 335) and add an inline note next to ADR 005 (or a small `docs/architecture/plugin-imports.md` companion) that lists the **realistic** allow-list (`ports/`, `models/`, `events.py`, `hooks.py`, named `config` factories). Do NOT yet ship a Plugin SDK (deferred to Phase 6).
6. **Misleading "capability registry" wording in connector permissions** (audit C8). Apply the rename listed in the §3 Phase 0 vocabulary table (`action_registry_unavailable_at_permission_validation`) and update the related test assertion if any test pins the literal string.

**Out of scope.**

- No new tool execution path (that is Phase 3).
- No removal of `orchestrator/plugins/graph/` (FP-031 stays open per the follow-up portfolio; revisit after `service` mode burns in for one release per ADR 011).
- No new public docs for third-party plugin authors.

**Exit criteria.**

1. CI fails on any new `routes/* → adapters/*` import (B2 closed as a CI gate).
2. CI fails when `services/lumogis-graph/models/{webhook,capability}.py` drift from canonical Core copies (C11 / B7 closed).
3. A bad-manifest fixture fails CI; a good-manifest fixture passes (C7 / B3 closed at the CI level).
4. `ARCHITECTURE.md` and ADR 005 no longer contradict `plugins/graph/__init__.py` line 21 (C9 doc slice closed).
5. No log line, response header, or test assertion in `connector_permissions.py` says "capability registry" when it means "action registry" (C8 closed).
6. All existing tests still pass; no behaviour change to in-process tool execution or graph mode.

**Chunk 2 (`architecture_import_boundary_tests`) — implemented 2026-04-25:** `orchestrator/services/signal_source_detection.py` (adapter imports only here); `routes/signals.py::_detect_source` delegates to it; `orchestrator/tests/test_routes_no_adapter_imports.py` (AST); `orchestrator/tests/test_vendored_models_in_sync.py` (byte identity vs `make sync-vendored` recipe); `orchestrator/tests/test_capability_manifest_validation.py` (valid/invalid Pydantic `CapabilityManifest` fixtures — not via `CapabilityRegistry`; production JSON Schema meta-validation remains a follow-up per `capability_registry.py` comment). In-scope items 5–6 (plugin text + `connector_permissions` wording) were **Chunk 1**, not this chunk.

---

### Phase 2 — Unified self-hosted tool catalog (read-only)

**Purpose.** Build *one coherent view* of every callable tool in the deployment **before** changing how tools are invoked. The catalog is the read model the LLM loop and Lumogis Web can both consume; execution unification (Phase 3) is layered on top.

**In scope.**

1. **Inventory current tool sources** in a small design doc (suggested slug `unified_tool_catalog`). Sources to enumerate:
   - `orchestrator/services/tools.py::TOOL_SPECS` and the mutable `TOOLS` openai-definition list (audit C3 / `tools.py` lines 354–361).
   - Plugin-registered tools (in-process) via `Event.TOOL_REGISTERED` → `_add_plugin_tool`.
   - The action registry (`actions.registry.list_actions`) and per-user permission rows (`connector_permissions`).
   - MCP-exposed tools (`mcp_server.MCP_TOOLS_FOR_MANIFEST`).
   - `CapabilityRegistry` discovered tools (`services/capability_registry.py::get_tools`).
   - The graph proxy ToolSpec built by `register_query_graph_proxy` when `GRAPH_MODE=service`.
2. **Define a `ToolCatalog` (or equivalent) read model** that answers:
   - **What tools exist?** A flat list of `(name, source, transport, connector, action_type, mode, version)`.
   - **Where did each come from?** `core | plugin | mcp | capability:<service_id> | proxy:<service_id>`.
   - **Which transport can expose each?** `llm_loop | mcp_surface | both | catalog_only`.
   - **Which connector / action / permission governs it?** Cross-reference into `actions.registry` and `connector_permissions` for a given `user_id`.
   - **Is it available for *this* user *right now*?** Combines `connector_permissions` + capability health (if applicable) + credential resolution.
   - **What is its origin tier?** `local | plugin | mcp_only | capability_backed`.
3. **Implementation should be read-only / catalog-only** in this phase. It must not change which tools the LLM actually receives, which MCP tools answer, or which executor is invoked. The catalog **observes** the existing wiring.
4. **Deterministic output is mandatory.** The catalog must produce deterministic ordering for tests, diagnostics, and Web rendering — for example by sorting by source tier (`core` → `plugin` → `mcp` → `proxy:<id>` → `capability:<id>`) and then by tool name — rather than relying on `dict` or registry iteration order. This makes the four Phase-2 tests stable, lets the Phase-4 `/api/v1/me/tools` view render predictably, and keeps OpenAPI snapshot diffs minimal as the catalog evolves.
5. **Document the difference** between *catalog* (what exists), *execution* (how it runs), *action* (what audit / Ask-Do gate applies), *connector* (which secret material), and *capability* (which out-of-process service supplied it). One section in this plan or a `docs/architecture/tool-vocabulary.md` companion.
6. **Do not over-engineer.** The catalog is a pure Python object built on demand from the live registries. No new database table. No schema migration. No external manifest format.
7. **Keep existing working tool execution intact.** Phase 2 lands as observability + diagnostics.

**Out of scope.**

- Changing `loop.py` to use the catalog.
- Changing `mcp_server.py` to consume the catalog.
- Inventing a marketplace, plugin SDK, or external manifest format.
- Adding a new transport (gRPC, WebSockets, etc.).

**Exit criteria.**

1. A test (e.g. `tests/test_tool_catalog_includes_core_tools.py`) proves the catalog contains every current in-process `TOOL_SPECS` entry by name in a **deterministic** order (same as item 4 above — stable across runs and platforms).
2. A test (`tests/test_tool_catalog_includes_plugin_tools.py`) proves a plugin-registered tool (use the existing graph plugin in `inprocess` mode, or a stub) appears with `source=plugin`.
3. A test (`tests/test_tool_catalog_mcp_vs_llm.py`) compares the MCP-visible tool set and the LLM-visible tool set, **explicitly listing intentional differences** (e.g. MCP exposes `memory.search` but the LLM tool loop does not, by design — ADR 010 §"What is NOT in scope here").
4. A test (`tests/test_tool_catalog_capability_discovery.py`) proves a mock capability service registered via `httpx.MockTransport` shows up in the catalog as `source=capability:<id>` with `transport=catalog_only` (i.e., **discovered but not yet executable** — bridging that gap is Phase 3).
5. `docs/architecture/tool-vocabulary.md` (or this plan's §3 Phase 0 vocabulary table) is the canonical glossary; both `ARCHITECTURE.md` and Lumogis Web docs reference it.
6. Existing tool tests (`test_loop_*`, `test_mcp_tools.py`, `test_query_graph_proxy.py`, `test_run_tool_requires_user_id`) are unchanged and still pass.

**Chunk 3 (`unified_tool_catalog`) — implemented 2026-04-25:** `orchestrator/services/unified_tools.py` (`ToolCatalog`, `ToolCatalogEntry`, `build_tool_catalog` / `build_tool_catalog_for_user`); tests `orchestrator/tests/test_tool_catalog_includes_core_tools.py`, `test_tool_catalog_includes_plugin_tools.py`, `test_tool_catalog_mcp_vs_llm.py`, `test_tool_catalog_capability_discovery.py`; `docs/architecture/tool-vocabulary.md`; links from `ARCHITECTURE.md` and `clients/lumogis-web/README.md`. `CapabilityRegistry` data via `all_services()` (not `get_tools()`) so each tool row keeps `capability_id`. Per-user permission / Ask-Do in the catalog is deferred (rows use `permission_mode="unknown"`). In-process `ToolSpec` has no plugin bit — non-core, non–KG-proxy tools are classified as `source=plugin` (see vocabulary doc).

---

### Phase 3 — Unified execution path for self-hosted tools

**Phase 3 in one line.** **First** build the execution bridge (generalised proxy, executor, permission + audit) **without changing the chat hot path**; **then** wire the LLM and MCP tool views **behind an env flag**, preserving **byte-identical** `tools=` behaviour for in-process / plugin tools when **no** capability service is healthy. This is deliberately the same split as **Chunk 4** (execution bridge) and **Chunk 5** (LLM / MCP wiring).

**Purpose.** Make the main Lumogis tool path coherent and policy-controlled. Generalise the **already-shipped** `register_query_graph_proxy` pattern so a healthy capability service's tool can run from chat with the same `user_id` / `permission_check` / `audit_envelope` semantics as an in-process tool — without inventing a new framework.

**In scope (two implementation steps).**

- **Step A — execution bridge, chat path unchanged:** Items 1–2 below (minimal execution layer + generalised graph proxy). `loop.py` and default LLM / MCP tool lists stay as today until Step B. Chunk 4 maps to this step.
- **Step B — wire LLM / MCP, flag-gated parity:** Items 3–6 below. Land wiring behind a flag (e.g. `LUMOGIS_TOOL_CATALOG_ENABLED`); with the flag off or with no healthy capability, behaviour matches pre–Phase-3 baselines. Chunk 5 maps to this step.

1. **Introduce a minimal execution layer.** Suggested module slug: `orchestrator/services/unified_tools.py` (or a new package `orchestrator/execution/`). Suggested shape (names indicative; the implementation plan will pin them):
   - `ToolCatalogBuilder` — reads the sources enumerated in Phase 2 and emits a per-request catalog snapshot.
   - `ToolExecutor` — given `(tool_name, input_, *, user_id, role, request_id)`, dispatches to the right handler and returns a uniform result envelope.
   - `CapabilityHttpToolProxy` — the generalised graph-proxy pattern: a `ToolSpec` whose handler POSTs to a capability service's `/tools/<name>` endpoint with the user's identity in the request envelope (e.g. `X-Lumogis-User`, mirroring the KG `WebhookEnvelope` pattern from ADR 011).
   - `PermissionCheck` — wraps the existing `permissions.check_permission` so the executor cannot bypass Ask/Do (ADR 006).
   - `AuditEnvelope` — wraps `actions.audit` so OOP tool calls emit the same correlation id + `user_id` + `tool_name` + `result_status` as in-process action handlers (ADR 019).
2. **Generalise the existing graph proxy pattern** instead of inventing a new abstraction from scratch. `register_query_graph_proxy` already shows the right shape (schema parity, hard timeout, per-user header, fail-soft on unhealthy). The new code lifts that into a reusable `CapabilityHttpToolProxy` so the **next** capability does not need bespoke wiring.
3. **Wire the LLM loop to consume the unified or expanded catalog.** Two safe options (the implementation plan picks one):
   - **(a)** `loop.ask` / `ask_stream` switch from `from services.tools import TOOLS` to `ToolCatalogBuilder.for_user(user_id).llm_view()`.
   - **(b)** Keep `from services.tools import TOOLS` but have `services.tools.TOOLS` itself be assembled from the catalog at process start so behaviour for in-process tools is byte-identical and capability tools are merged in only when healthy.
4. **Capability-backed tools must be executable only when:**
   - The capability service is **healthy** at the moment the tool list is rendered for the LLM (existing 60 s health probe is the source of truth).
   - The manifest has passed validation (Phase 1 CI gate is the long-term guarantee; runtime soft-validate is acceptable).
   - The user is **authorised** through `connector_permissions` + capability tier rules.
   - Required credentials are available (`resolve_runtime_credential` returns a value at user → household → system → env precedence; ADR 027).
   - **Timeout and failure behaviour are defined** — adopt the KG `/context` pattern: hard `httpx` timeout, soft-unavailable string back to the LLM (today the graph proxy returns `"graph not configured / unavailable"` and this works in chat).
   - **Audit / correlation is emitted** — the `AuditEnvelope` writes a row keyed on `(request_id, user_id, tool_name, capability_id)`.
5. **MCP surface alignment.** Either:
   - **(a)** `mcp_server.py` consumes the same catalog and exposes a documented filtered view (e.g. read-only community tools only — preserving ADR 010 §"What is NOT in scope here"), **or**
   - **(b)** MCP keeps its own hand-coded tool list but a `test_tool_catalog_consistency` test (built in Phase 2) documents and verifies the intentional divergence so it cannot drift silently.
   The Phase-3 implementation plan must pick one and document why.
6. **Preserve current in-process behaviour during migration.** The first commit that wires the LLM loop to the catalog must produce a byte-identical `tools=` array for in-process / plugin tools when no capability service is healthy. Capability tools join the array only when the registry reports healthy.

**Out of scope.**

- A second non-graph capability service in production.
- Outbound webhook fan-out to arbitrary URLs (audit B9 — deferred).
- Marketplace plugin loading.
- Replacing the action registry with the catalog (the catalog *references* the action registry; it does not replace it).
- Async/await migration of services/adapters (DEBT.md "sync/async consistency" stays open per ADR 010).

**Exit criteria.**

1. A test using `httpx.MockTransport` registers a mock capability service publishing one tool; the tool appears in the catalog (Phase 2 already proved this for catalog visibility) **and** the LLM tool loop receives it when the mock service is healthy.
2. The LLM tool loop **does not** receive that tool when the mock service reports unhealthy or its manifest is invalid (fail-closed verified).
3. Permission check is exercised — a user without the relevant `connector_permissions` row gets the existing Ask flow, not a silent execute.
4. `AuditEnvelope` emits a row (or structured log record per ADR 019) that includes `request_id`, `user_id`, `tool_name`, `capability_id` for one mock OOP tool execution.
5. Existing in-process tools (`search_files`, `read_file`, `query_entity`, plus plugin-registered tools) still work byte-identically — `make compose-test` passes with no new failures.
6. The graph `query_graph` tool (proxy mode under `GRAPH_MODE=service`) keeps working through the **generalised** pattern; no graph-specific shortcuts left in `services/tools.py` once the proxy is rebuilt on top of `CapabilityHttpToolProxy`.
7. MCP behaviour is unchanged for external clients (Claude Desktop, Thunderbolt). If MCP migrates to the catalog (option 5a above), the MCP tool list as observed via `GET /capabilities` is byte-identical to the pre-migration manifest.

**Chunk 4 / Phase 3A (`unified_tool_execution_plane`) — implemented 2026-04-25:** `orchestrator/services/capability_http.py` — `post_capability_tool_invocation`, :class:`CapabilityHttpToolProxy`, :class:`HttpInvokeResult`, `graph_query_tool_proxy_call` (KG keeps optional bearer when `GRAPH_WEBHOOK_SECRET` unset; generic path defaults to fail-closed without service auth). `orchestrator/services/execution.py` — :class:`ToolExecutor`, :class:`PermissionCheck`, :class:`ToolAuditEnvelope` (injectable sink; **OOP** `audit_log` fan-in added 2026-04-26 in `audit_log_oop_fanin`). `services/tools._query_graph_proxy_handler` delegating to `graph_query_tool_proxy_call` (schema and `register_query_graph_proxy` preserved). New tests: `test_capability_http_tool_proxy.py`, `test_tool_executor_permission_check.py`, `test_tool_executor_audit.py`. The full Phase 3 **exit list above** (e.g. LLM loop ingesting capability tools) is **not** in this chunk — that is Chunk 5+.

---

### Phase 4 — Lumogis Web as household control surface

**Purpose.** Make Lumogis Web a coherent first-party client without turning it into the architecture owner. The product surface drives which Core HTTP facades exist; it does not encode backend implementation.

**Dependencies.** Not all Web / API facade work must wait for Phase 3. **Independent** thin `/api/v1/*` facades (LLM provider summaries, notification settings, MCP token management, admin diagnostics, credential **metadata** curation) can start as soon as **Phase 1** boundary hygiene is acceptable — they do not require the tool catalog. **`/api/v1/me/tools`** and any view built from the **read-only** tool catalog **depend on Phase 2**. **Executable** capability tool state, fail-closed behaviour, and “why is this tool unavailable?” semantics **depend on Phase 3** (and Chunk 4–5). Phase 4 can schedule facade chunks in parallel with Phase 3 where those dependencies allow.

**In scope.**

1. **Lumogis Web should target stable `/api/v1/*`.** Catalogue every Web call site against the existing `REQUIRED_V1_PATHS` set (`orchestrator/tests/test_api_v1_openapi_snapshot.py`). Anything Web depends on that is **not** in `/api/v1/*` is either added to v1 (with a thin facade and a snapshot update) or marked as legacy and tracked.
2. **Surfaces Web should display through Core-owned APIs:**
   - User profile, sessions, JWT refresh (`/api/v1/auth/*`, `/api/v1/me/*` — already shipped).
   - Per-user connector credentials (curated read-only; never the raw ciphertext or plaintext payload — `/api/v1/me/connector-credentials/*` per ADR 020).
   - Per-user connector permissions and Ask/Do approvals (`/api/v1/me/connector-permissions/*`, `/api/v1/me/approvals/*`).
   - Notifications (ntfy + web-push status) — `/api/v1/me/notifications` (read-only façade; edit paths remain Connectors / future chunk).
   - MCP tokens — `/api/v1/me/mcp-tokens` (mint, list, revoke per ADR 017).
   - Admin and diagnostics — `/api/v1/admin/*` (gradual migration of the legacy HTML admin to JSON; HTML admin **stays** during this phase, see "Out of scope").
   - **Capability status / available tools view** — built from the Phase-2 catalog.
3. **Web should not encode deep backend implementation details.** Concretely: Web does not know the credential ciphertext layout, the FalkorDB schema, the Qdrant payload shape, or which `services/*` function ran. It calls `/api/v1/me/connector-credentials/ntfy` and gets a JSON shape Core controls.
4. **Where Web needs curated user-facing views, add thin Core facades** rather than expose raw stores. Examples to seed (each becomes its own small chunk if needed):
   - **Curated LLM provider key view** — wrap `services/connector_credentials.list_records('llm_*', user_id)` into one `/api/v1/me/llm-providers` GET that returns connector id + label + last-used + active-tier without ever returning ciphertext (FP-014, FP-015).
   - **Curated notification settings view** — collapse ntfy + future web push into one `/api/v1/me/notifications` view.
   - **Capability status / tool availability view** — `/api/v1/me/tools` returns the Phase-2 catalog filtered for the calling user. Used by Web to render the household's "what can the assistant do for me right now?" panel.
   - **Admin diagnostics view** — `/api/v1/admin/diagnostics` returns the dashboard health JSON (already partly there) so Web can render Settings without scraping the legacy HTML.
5. **Keep legacy dashboard / admin routes working** while migrating machine-readable surfaces to `/api/v1/admin/*` (audit B6, reprioritised to **P1 only when blocking Web**, otherwise **P2**). Caddy already routes legacy admin to the orchestrator (ARCHITECTURE.md §"Lumogis Web and Caddy").

**Out of scope.**

- Removing the legacy HTML admin (FP-031 / "remove in-Core graph plugin" is in the same family — defer until Web admin shell covers the same workflows).
- Building a public SPA framework for third-party plugin UIs (audit row 4 deferred to Phase 6).
- `mTLS` between Caddy and orchestrator (single host LAN deployment scope).

**Exit criteria.**

1. Every Web HTTP call resolves to a `/api/v1/*` path or is on a documented "legacy, planned migration" list.
2. The list of *missing* thin v1 facades for Web is captured (e.g. as plan slugs `admin_diagnostics_v1`, `me_llm_providers_v1`, `me_tools_v1`, `me_notifications_v1`) and entered into the follow-up portfolio via `/verify-plan` once the relevant chunk ships.
3. Web does **not** decrypt or display credential ciphertext shape; the curated views listed above own the JSON contract.
4. The `/api/v1/me/tools` view is wired to the Phase-2 catalog and rendered in Lumogis Web's Settings → Capabilities tab (or equivalent), showing `(name, source, available, why_not_if_unavailable)`.
5. Admin-only views are rendered behind `require_admin` (ADR 012); members see a 403 + curated empty state, never a partial admin shell.
6. `make web-test` and `make compose-test` pass; the integration test (`tests/integration/test_caddy_security_headers.py`) and Playwright `admin_shell.spec.ts` continue to pass.

**Closeout review (2026-04-26):** [`phase-4-household-control-surface-closeout-review.md`](phase-4-household-control-surface-closeout-review.md) — coherence, safety, tests, and Phase 5 readiness (planning-first recommendation).

---

### Phase 5 — Optional capability services and household premium boundaries

**Status (2026-04-26):** **Sufficiently complete** for **self-hosted capability scaffolding** (discovery, generic invoke, catalog/diagnostics façades, permission **labelling**, OOP audit fan-in, KG body parity, dev-only compose smoke). **Not** a claim that every narrative bullet in Phase 6 deferrals is solved. **Phase 6 is not started.**

**Final programme closeout:** [`phase-5-final-capability-scaffolding-closeout-review.md`](phase-5-final-capability-scaffolding-closeout-review.md) (`phase_5_final_capability_scaffolding_closeout_review`).

**Planning + mock test slice (2026-04-26):** [`phase-5-capability-contract-reference-plan.md`](phase-5-capability-contract-reference-plan.md) — reference contract, matrix, and **implemented** test-only chunk **`capability_contract_mock_service_test`**. **Incremental closeout:** [`phase-5-mock-contract-closeout-review.md`](phase-5-mock-contract-closeout-review.md). **Remainders closed (`phase_5_remaining_capability_hardening`):** FU-3 catalog permission façade, FU-5 `capabilities_endpoint` documentation + discovery warning, FU-4 dev-only `services/lumogis-mock-capability` + `docker-compose.mock-capability.yml`. Slugs **`query_graph_body_parity_test_or_fix`**, **`audit_log_oop_fanin`**, and related FU rows are **done** per the Phase 5 plan §6b. Product-specific “real” capabilities and Phase 6 deferrals remain out of scope.

**Purpose.** Support optional out-of-process capabilities for self-hosted deployments **without** introducing cloud SaaS assumptions. Confirm `lumogis-graph` as the reference shape and prove the pattern generalises with one mock second capability — without forcing every operator onto premium overlays.

**In scope.**

1. **Keep KG service (`services/lumogis-graph/`) as the reference optional capability.** The webhook + `/context` + `/tools/query_graph` contract (ADR 011 §3) is the long-term boundary; the `GRAPH_MODE=inprocess|service|disabled` switch stays.
2. **Generalise only what is needed from the KG service pattern.** The Phase-3 `CapabilityHttpToolProxy` is the first generalisation. Other reusable bits to extract over time (each becomes a small chunk, not a framework):
   - `WebhookDispatcher` (`services/graph_webhook_dispatcher.py`) → keep KG-specific until a second capability needs it.
   - `CapabilityHealthSurface` extension on `GET /` and `GET /health` → already capability-agnostic (ADR 010 §3).
   - `CapabilityUILink` rendering (capability launchers) → already capability-agnostic per the `capability_launchers_and_gateway` plan.
3. **Capability services talk to Core through HTTP contracts, not shared DB access.** Stated in ADR 011 §1 ("phase 1; full database decoupling is deferred"). For *new* capability services, **disallow** issuing them Core's Postgres / Qdrant credentials by default. They must use the `/api/v1/*` HTTP surface for any per-user Core data they need (or own their own persistence as KG does for FalkorDB).
4. **Household-LAN trust model for capability services.** Define and document:
   - **Shared secret** in the bearer header (`GRAPH_WEBHOOK_SECRET` is the reference; `hmac.compare_digest` comparison; default-fail when unset and `KG_ALLOW_INSECURE_WEBHOOKS=false` per ADR 011 §3).
   - **`X-Lumogis-User`** request header for per-user attribution (already present in KG — see ADR 011 §3). **Security rule:** `X-Lumogis-User` is **accepted only on authenticated capability-service requests** (e.g. after shared-secret / webhook auth succeeds). It is an **attribution** header, **not** authentication. Core must **reject or ignore** `X-Lumogis-User` from unauthenticated or arbitrary LAN clients. Do not treat the header as proof of user identity on its own.
   - **No mTLS requirement by default.** mTLS / dynamic signed manifests / external broker integrations are deferred (Phase 6).
5. **Audit fan-in from out-of-process capabilities.** Specify the envelope: every capability that calls a Core write API is identified by capability id + correlation id + `user_id`; every audit row produced by a capability has `input_summary.capability_id = <id>`. Implement during Phase 3 wiring (the `AuditEnvelope` step) and revisit here for fan-in completeness.
6. **Health and capability discovery surfaced in Core and Web.**
   - `GET /` and `GET /health` already report `capability_services` (ADR 010).
   - The Phase-4 Web view `/api/v1/me/tools` shows the per-tool effect ("KG `query_graph` is unavailable because `lumogis-graph` is unhealthy").
   - The dashboard's existing "Capability services" card (per the `capability_launchers_and_gateway` plan) renders launcher metadata generically — keep this; do not migrate to a Web-only surface in this phase.

**Out of scope.**

- mTLS as a hard requirement.
- A signed manifest model (defer to Phase 6).
- Marketplace / package distribution for capability services.
- A second non-mock capability service in production (a mock is enough to prove the pattern; the second real capability is expected to be premium and ships outside this plan's scope).
- Multi-tenant tenant isolation between capabilities.

**Exit criteria.**

1. `lumogis-graph` is documented as the **reference** capability shape, with an explicit list of which contracts new capabilities must honour (`CapabilityManifest` shape, `/health` contract, optional `/webhook`, optional `/tools/<name>`, optional `/context`).
2. A second **mock** capability publishing one tool can be tested end-to-end through the Phase-3 `CapabilityHttpToolProxy` without graph-specific code paths in `services/tools.py` or `loop.py`.
3. New capability services do not require direct Postgres / Qdrant credentials. (`docker-compose.premium.yml` already gives the KG service its own role; document the rule for future overlays.)
4. Core can report **why** a capability tool is unavailable to a given user (one of: capability unhealthy, manifest invalid, permission denied, credential missing, timeout, OOP error); the `/api/v1/me/tools` view surfaces the reason.
5. The security model documented for capability services is appropriate for **household LAN** — shared secret plus **authenticated** per-request attribution (never standalone trust in `X-Lumogis-User`); mTLS is opt-in, not required.

---

### Phase 6 — Deferred ecosystem / marketplace work

**Purpose.** Preserve the audit's larger findings without pretending they are immediate. Each deferred item keeps its original audit ID, a stated reason for deferral, and a revisit trigger.

| Item | Audit ID(s) | Why deferred | Revisit trigger |
| --- | --- | --- | --- |
| Full public **third-party Plugin SDK** | B5 / C9 (full slice) | Household-scale Lumogis runs in-process plugins as fully-trusted code; ADR 005's filesystem boundary is sufficient for first-party + private out-of-tree plugins. A PyPI-distributable SDK introduces semver responsibilities Core does not yet need. | First non-core contributor wants to ship a plugin, **or** premium service ships outside the main repo and needs stable Core re-exports. |
| **Dynamic signed connector manifests** | B10 (full slice) / Audit §6 row "Premium connectors" | All connector ids are static (`connectors/registry.py`); per-user credentials are encrypted; per-user permissions are enforced. There is no household pressure to load connectors at runtime. | A non-core contributor wants to publish a connector pack, **or** product ships a marketplace UI. |
| **Arbitrary third-party connector sandbox** (subprocess + permissions + audit) | B10 | In-process fully-trusted code is acceptable for self-hosted; sandbox infrastructure (PID isolation, syscall filter, quota) is large work for low household yield. | Untrusted connector code is offered to install, **or** a community plugin ecosystem launches. |
| **Marketplace distribution** (signed manifests, PyPI / OCI registry, install UX) | Audit §15 row B5 + §6 "Third-party UI modules" | Same as above; no marketplace exists yet. | Marketplace is announced. |
| **Cloud / hosted multi-tenant admin model** | Audit §6 row "Premium services" + Multi-User audit Phase C (ten items: `tenant_id`, RLS, tamper-evident audit, OIDC, per-user Qdrant collections, per-user FalkorDB graphs, multi-tenant dashboard, secret store, per-user quotas, policy engine) | Lumogis is **not** a cloud SaaS; ADR 012 closes Phase A and explicitly defers Phase C. `MULTI-USER-AUDIT-RESPONSE.md` §4 records this. | Public hosted Lumogis offering is planned, **or** multiple households share one Core instance, **or** household LAN audit needs tenant-scoped RLS. |
| **External event webhook marketplace** (outbound webhooks to arbitrary URLs from Core) | B9 | Today, signals fan in; Core does not currently push events to outside URLs. This becomes interesting only when external automation runners want to subscribe. | Revisit when Lumogis needs **outbound events** for a **household automation runner** such as **Activepieces**, **n8n**, **Home Assistant**, **Node-RED**, or **another local/private automation** service — not only generic “external” SaaS. |
| **mTLS as a default requirement** for every deployment | Audit §10 + §15 row B6 partial | Household LAN trust model treats compose network as the security boundary; mTLS adds operator burden disproportionate to risk. | Deployment crosses the household LAN (e.g., capability service runs on a separate network segment), **or** a security-conscious operator wants it as opt-in. |
| **Headless commercial distribution model** (no HTML admin) | Audit §6 row "Dashboard / static admin" + B6 full slice | The legacy HTML admin still serves real workflows; phasing it out is a Lumogis-Web-replaces-it migration that lives in Phase 4 and beyond. | Web admin shell covers every workflow currently in the legacy HTML admin, **and** at least one operator asks for `LUMOGIS_HEADLESS=true`. |
| **Public SaaS-grade tenant isolation** (per-user Qdrant collections, per-user FalkorDB graphs, RLS on Postgres, OIDC IdP per household, per-user quotas) | Multi-User audit Phase C C8 / C9 + Audit §17 §17.4 row | These are public-multi-tenant defence-in-depth patterns; ADR 015 (`scope` columns + visibility filters) is the household-correct fix and is shipped. | Same trigger as "cloud / hosted multi-tenant admin model" above. |
| **Full sync→async migration of `services/*` and `routes/*`** | DEBT.md "sync/async consistency" + ADR 010 §"Known technical debt" | Synchronous adapters are adequate for household-scale concurrency; the migration is large and disruptive. | First multi-user deployment requirement that exceeds threadpool capacity (per ADR 010), **or** the project commits to hosted operation. |

For each item: the original audit row stays valid; this plan does **not** request a re-write of the audit. The deferral is captured here and will be referenced from `/verify-plan` follow-up rows so the portfolio remembers why work is paused.

---

## 4. Reprioritised backlog table

Bucket key: **P0** must do before more capability growth · **P1** needed for coherent self-hosted platform · **P2** useful after Web / tool plane stabilises · **P3** defer until third-party ecosystem or cloud posture · **P4** document only / watch.

Action key: **Implement now** (in this remediation programme) · **Implement soon** (next 1–2 chunks after the current critical path) · **Keep** (the audit row is already correct; carry the rationale forward) · **Defer** (move to Phase 6 with revisit trigger).

| Audit ID | Finding title (audit) | Original severity | Revised priority | Revised phase | Action | Rationale | Suggested implementation plan name |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **B1 / C1** | Unify tool execution (LLM + MCP + OOP on one path) | 🚨 Blocking | **P1** | Phase 3 | Implement now (after Phase 0–2) | The household needs a coherent agent loop more than a public ecosystem; unifying execution unlocks the OOP capability path without rebuilding it per service. | `unified_tool_execution_plane` |
| **B2 / C2** | Route-layer enforcement (no `routes/* → adapters/*`) and `routes/signals.py::_detect_source` fix | ⚠️ Important | **P0** | Phase 1 | Implement now | Stops further drift; CI gate cheap to add; pays off every refactor afterwards. | `architecture_import_boundary_tests` |
| **B3 / C7** | Capability manifest meta-validation in CI | ⚠️ Important | **P0–P1** | Phase 1 | Implement now (CI fixture); P1 for prod soft-validate | Bad manifests already silently land — the CI gate is the household-honest bar. | `capability_manifest_contract_hardening` |
| **B4** | OOP tool catalog visible in chat | ⚠️ Important | **P1** | Phase 3 | Implement now | Same as B1 — capability discovery exists; making it executable from chat is the next coherent step. | `ecosystem_capability_tool_bridge` (or merged into `unified_tool_execution_plane`) |
| **B5 / C9 (doc slice)** | Plugin import-boundary clarification (ADR + ARCHITECTURE.md) | 🟡 Medium | **P0** (doc) | Phase 0–1 | Implement now (docs + non-behavioural terminology cleanup) | Cheap; removes a contradiction every contributor can see. | (within `phase_0_audit_vocabulary_and_backlog_mapping`) |
| **B5** (full slice) | Public Plugin SDK on PyPI | 🟡 Medium | **P3** | Phase 6 | Defer | Household scale does not justify SDK semver overhead; first-party plugins live in-tree or as private out-of-tree directories. | (deferred — revisit when external plugin author appears) |
| **B6** | Public operator API (`/api/v1/admin/*` for all JSON ops) | 🟡 Medium | **P1 only when blocking Web; else P2** | Phase 4 | Implement soon (per Web need) | Web is the driver; only the JSON facades Web actually needs are P1, the rest is P2. | `admin_shell_api_v1` (already proposed in audit; carry forward) |
| **B7 / C11** | Vendored model drift CI for `webhook.py` + `capability.py` | 🟡 Medium | **P0** | Phase 1 | Implement now | Drift will silently break KG; Make target already exists, only the CI gate is missing. | `kg_service_contract_extraction_ci_gate` (or extend the existing `kg_service_contract_extraction` plan) |
| **B8** | OpenAPI / "stable vs unstable" labels for legacy routes | 🟡 Medium | **P2** | Phase 4 | Implement soon (when Web migration touches each route) | Tightly coupled to the Web migration; can land per route as Web converges. | `api_v1_backward_compatibility_tests` (extend existing) |
| **B9** | Outbound event webhooks to external runners | 🟡 Medium | **P3** | Phase 6 | Defer | No household consumer asking for it; opens marketplace-scale concerns. Revisit when outbound events are needed for local/private automation (see Phase 6 B9 row). | `event_schema_contracts` (deferred plan slug) |
| **B10** | Third-party connector sandbox + dynamic registration | 🚨 Blocking (audit) | **P3** | Phase 6 | Defer | Static `connectors/registry.py` + per-user credentials + per-user permissions are sufficient for household trust; sandbox is large + premature. | `connector_contract_unification` (deferred) |
| **C3** | Global mutable `TOOL_SPECS` / `TOOLS` | ⚠️ Important | **P1** | Phase 2–3 | Implement now (subsumed by catalog) | The catalog snapshot is the right shape; no separate fix needed once Phase 2–3 lands. | (subsumed by `unified_tool_catalog` and `unified_tool_execution_plane`) |
| **C4** | Schema duplication: graph in-process vs proxy | 🟡 Medium | **P1** | Phase 3 | Implement now (subsumed by general proxy) | Fixed by definition once `CapabilityHttpToolProxy` generalises the pattern. | (subsumed by `unified_tool_execution_plane`) |
| **C5** | Signal monitors import concrete adapters | 🟡 Medium | **P2** | Phase 1 (allow-list note) → Phase 4 (refactor) | Implement soon (allow-list); Implement later (refactor) | Signals are not a near-term ecosystem surface; document the exception today and refactor when a real backend swap appears. | `signal_source_factory_refactor` (P2) |
| **C6** | Direct SQL in `services/tools._query_entity` | 🟡 Medium | **P2** | Phase 4–5 | Implement soon (when entities surface gets a thin facade for Web) | Not blocking; the Web entities view will likely demand a `services/entities` repository anyway. | `entities_service_repository_extraction` |
| **C8** | "Capability registry" naming mismatch in `connector_permissions` | 🟡 Medium | **P0** | Phase 0 / Phase 1 | Implement now (rename + tests) | Cheap; removes operator confusion immediately. | (within `phase_0_audit_vocabulary_and_backlog_mapping` or `architecture_import_boundary_tests` ship) |
| **C10** | MCP and LLM tool lists diverge by design | ⚠️ Important | **P1** | Phase 2 (test) → Phase 3 (decision) | Implement now (consistency test); decide unification or filtered view in Phase 3 | Divergence is acceptable when **documented and tested**; it becomes a problem when silent. | (covered by `unified_tool_catalog` consistency test) |
| **C12** | Config singletons in `services/tools` | 🟡 Medium | **P2** | Phase 3+ | Implement soon (incremental during catalog wiring) | Solving inside the catalog/executor refactor is cleaner than retrofitting per-call injection. | (covered by `unified_tool_execution_plane`) |
| Audit §6 — Third-party clients | "Possible but fragile" without single integrator doc | — | **P2** | Phase 4 | Implement soon (after Web's v1 surface is mostly stable) | The doc should describe what household clients can rely on; deferring until v1 stabilises avoids re-writing it. | `core_integrator_v1_doc` |
| Audit §6 — Third-party UI modules | iframe / separate SPA contract | — | **P3** | Phase 6 | Defer | No household user asking for UI plugins. | (deferred) |
| Audit §6 — External automation | Outbound webhooks | — | **P3** | Phase 6 | Defer | Same as B9 (revisit trigger: household/local automation runners — see Phase 6 B9 row). | (deferred) |
| Audit §6 — Deployment bundles | Validated env schema, `lumogis config validate` | — | **P2** | Phase 4 | Implement soon | Very useful for household ops; not on the critical path. | `lumogis_config_validate` |
| Audit §10 — `mTLS` + signed requests for OOP | — | — | **P3** | Phase 6 | Defer | Household LAN does not require mTLS by default; opt-in only. | (deferred) |
| Audit §15 row B5 (Plugin SDK on PyPI) | — | — | **P3** | Phase 6 | Defer | Same as B5 full slice. | (deferred) |
| Audit §15 row B6 (Public operator API) | — | — | **P1/P2** | Phase 4 | Per Web need | See B6. | `admin_shell_api_v1` |
| Audit §15 row B9 (Outbound webhooks) | — | — | **P3** | Phase 6 | Defer | See B9. | (deferred) |
| Audit §15 row B10 (Connector sandbox) | — | — | **P3** | Phase 6 | Defer | See B10. | (deferred) |
| Multi-user audit Phase C (10 items) | Hosted multi-tenant foundations | — | **P3 / P4** | Phase 6 (P3) or watch (P4) | Defer | Confirmed deferred by ADR 012 + `MULTI-USER-AUDIT-RESPONSE.md` §4. Carries `FP-004` / `FP-045`. | (deferred — revisit when product direction shifts) |
| DEBT.md "sync/async consistency" | Full sync→async migration | Open | **P4** | Phase 6 | Defer (watch) | Triggered by hosted scale-out, per ADR 010. | (deferred) |
| DEBT.md "graph stats `user_id='default'`" (FP-042) | — | — | **P1** | Phase 3 (folded in) | Implement soon | One-line Cypher fix; should land alongside the Phase-3 audit envelope work. | (folded into `unified_tool_execution_plane` or its own follow-up) |

The reprioritisation buckets above are consistent with the user's expected reordering:

- **B1 / C1 unified tool execution → P1.** ✅ (P0 is reserved for boundary hygiene; B1 is the next bar.)
- **B2 route-layer enforcement → P0.** ✅
- **B3 manifest validation → P0/P1.** ✅ (P0 for the CI fixture; P1 for production soft-validate.)
- **B4 OOP tool catalog in chat → P1.** ✅
- **C8 naming mismatch → P0 quick fix.** ✅
- **C9 plugin boundary clarification → P0/P1.** ✅ (P0 for the doc slice; full SDK is P3.)
- **B5 full Plugin SDK → P3.** ✅
- **B6 public operator API → P2 (P1 only when Web requires it).** ✅
- **B9 outbound event webhooks → P3.** ✅
- **B10 third-party connector sandbox → P3.** ✅
- **Dynamic connector registration → P3.** ✅
- **Cloud multi-tenant posture → P3 / P4.** ✅

---

## 5. Current-state architecture summary

### What is already good (do not regress)

- **Ports + `config.py` factory** (`orchestrator/ports/`, `orchestrator/config.py`). Swapping vector / metadata / embed / LLM / graph / notifier backends is one adapter + one factory branch.
- **First-party web client contract gate** (`orchestrator/tests/test_api_v1_openapi_snapshot.py`, `clients/lumogis-web/openapi.snapshot.json`, `Makefile` `web-codegen-check`). The OpenAPI snapshot stops accidental drift.
- **Multi-user shipped** (ADRs 012, 013, 015, 017, 018, 022, 024, 025, 026, 027). Identity, per-user credentials, per-user permissions, per-user MCP tokens, per-user batch jobs, per-user file index, memory scopes, and per-user notifier targets all exist and are tested.
- **Knowledge Graph already extracted to an out-of-process service** (`services/lumogis-graph/`) with the `WebhookEnvelope` + `/context` + `/tools/query_graph` contract, the `GRAPH_MODE` switch, and the `register_query_graph_proxy` pattern (ADR 011). This is the **shape** every future capability should follow.
- **Capability discovery and health surface** (`services/capability_registry.py`, dashboard health card, `GET /` and `GET /health` extensions) are in place and additive (ADR 010).
- **Same-origin Caddy + cookies + CSRF + per-user MCP tokens** — the household trust model is internally consistent (ADR 012, ADR 017, `csrf.py`).
- **Audit log + Ask/Do + routine elevation** (`actions/audit.py`, `permissions`, ADR 006, ADR 019). Append-only domain audit + structured logs; `mark_reversed` is per-user gated; correlation middleware is wired.

### What is partially modular (improve here)

- **Catalog permission truth.** **Improved (2026-04-26, FU-3):** `build_tool_catalog_for_user` sets `permission_mode` (`ask` / `do` / `blocked` / `unknown`) from `get_connector_mode` where `connector` is known; MCP-only rows stay `unknown`. Per-user **execution** rules unchanged.
- **Global vs per-user catalog.** Discovery and merge rules are largely instance-wide; `prepare_llm_tools_for_request` documents `user_id` as reserved for future filtering.
- **MCP and LLM tool lists.** Still diverge by design; Phase 2 tests (`test_tool_catalog_mcp_vs_llm.py`) pin intentional differences. Phase 3B did **not** migrate MCP onto the unified catalog (`test_mcp_manifest_unchanged.py` guards stability).
- **OOP audit persistence.** `try_run_oop_capability_tool` emits `oop_tool_audit` **and** durable `audit_log` rows (`tool.execute.capability` via `persist_tool_audit_envelope` → `write_audit`; 2026-04-26, `audit_log_oop_fanin`). Direct `ToolExecutor` calls without the OOP bridge still use an injectable envelope sink only.
- **Admin / operator surfaces** mix legacy HTML and `/api/v1/admin/*`. Phase 4 migrates per Web need.

### What was paper-modular (now gated — do not regress)

- **`ARCHITECTURE.md` vs graph `import config`.** Documented in `docs/architecture/plugin-imports.md` (Phase 0 / Chunk 1); link from `ARCHITECTURE.md` is the contributor path.
- **Route-layer adapter imports.** Chunk 2 moved RSS/page-scrape detection into `services/signal_source_detection.py`; `test_routes_no_adapter_imports.py` enforces the rule.
- **Capability manifest shape.** Chunk 2 added `test_capability_manifest_validation.py` (CI fixture); runtime soft-validate remains acceptable per Phase 3.
- **Vendored KG wire models.** Chunk 2 added `test_vendored_models_in_sync.py` alongside `make sync-vendored`.

### What is risky if we add more features now

- **Adding a second non-graph capability service before Phase 3** — every capability would need bespoke proxy wiring (graph-shaped today). Doing the catalog + executor work first means the next capability is small.
- **Adding a public Plugin SDK before fixing the `import config` contradiction** — documents the wrong contract.
- **Letting Lumogis Web grow legacy admin URL dependencies** — every URL Web learns is one more route the v1 migration must cover. Phase 4 should run **alongside** Web feature growth, not after.
- **Migrating to async at this point** — confirmed by ADR 010 and DEBT.md; trigger is hosted scale-out, not household feature growth.
- **Removing `orchestrator/plugins/graph/`** before `service` mode burns in for a release (FP-031 stays open per ADR 011).

### What must not be rewritten yet

- **Existing in-process KG path** — keep `GRAPH_MODE=inprocess` byte-stable; it is the household default. ADR 011 defers plugin removal explicitly.
- **The five-pillar contributor model** (`services / adapters / plugins / signals / actions`) — keep. The unified tool catalog and execution router are **overlays**, not replacements.
- **Lumogis Web is not the architecture owner.** It consumes Core; it does not encode credential payload structure or replace `services/*` logic.
- **Marketplace-grade plugin infrastructure** — see Phase 6.
- **`docker-compose.yml` topology** for the default profile — Caddy + lumogis-web + orchestrator + Postgres + Qdrant + Ollama is the household reference and is stable.
- **Cloud multi-tenant posture** — not now; not in this plan.

---

## 6. Recommended immediate implementation order

Each chunk below is intended to be turned into one `/create-plan` invocation. The slugs are suggested; the implementation plan picks the final names.

### Chunk 1 — Audit vocabulary and backlog mapping

**Suggested slug:** `phase_0_audit_vocabulary_and_backlog_mapping`.

- **Scope**
  - Create / update `docs/architecture/lumogis-self-hosted-platform-remediation-plan.md` (this file; further refinement only if review needs it).
  - Land the **terminology cleanup list** from §3 Phase 0 as a small set of focused edits:
    - `ARCHITECTURE.md` line 335 plugin allow-list correction (or a side-by-side `docs/architecture/plugin-imports.md` companion that ARCHITECTURE.md links to).
    - `connector_permissions.py` rename (log key + advisory header) and any test assertion that pins the old string.
  - Optional: a one-page contributor cheat-sheet `docs/architecture/tool-vocabulary.md` (or fold into ARCHITECTURE.md) defining `Tool`, `Action`, `Connector`, `Capability`, `Plugin`, `Signal`, `Routine`, `MCP surface`.
- **Explicit non-goals**
  - No code change to `loop.py`, `services/tools.py`, `mcp_server.py`, or `capability_registry.py`.
  - No change to capability discovery or LLM behaviour.
- **Files likely touched**
  - `docs/architecture/lumogis-self-hosted-platform-remediation-plan.md` (this file).
  - `ARCHITECTURE.md` (line 335 + nearby).
  - `docs/architecture/plugin-imports.md` (new, optional).
  - `docs/architecture/tool-vocabulary.md` (new, optional).
  - `orchestrator/routes/connector_permissions.py` (rename log key + header constant).
  - `orchestrator/tests/test_per_user_connector_permissions.py` and any neighbouring test that pins the literal "capability registry" string.
- **Tests required**
  - Existing `test_per_user_connector_permissions*` tests pass with the rename.
  - `make test` / `make compose-test` continue to pass.
- **Rollback risk**
  - Very low (documentation + log-string rename).
- **Acceptance criteria**
  - The §3 Phase 0 terminology table no longer flags any unresolved row.
  - No test or code references "capability registry" where the action registry is meant.

### Chunk 2 — Boundary hygiene

**Suggested slug:** `architecture_import_boundary_tests`.

- **Scope**
  - **Refactor** `routes/signals.py::_detect_source` to depend on a service helper (e.g. `services/signal_source_detection.detect_source`) that owns the adapter imports; the route no longer imports `adapters.rss_source` / `adapters.page_scraper`.
  - **Add** `orchestrator/tests/test_routes_no_adapter_imports.py` (AST scan of every file under `orchestrator/routes/` that asserts no module imports `adapters.*`).
  - **Add** `orchestrator/tests/test_vendored_models_in_sync.py` that builds the same bytes as `make sync-vendored` and compares to `services/lumogis-graph/models/{webhook,capability}.py` (implemented; path under `orchestrator/tests/`, not `tests/integration/`).
  - **Add** `orchestrator/tests/test_capability_manifest_validation.py` with one good and one bad `CapabilityManifest` fixture (Pydantic validation; **implemented**).
- **Explicit non-goals**
  - No change to LLM tool execution.
  - No change to capability discovery behaviour beyond the manifest fixture.
  - No new SDK package.
  - No removal of legacy admin routes.
- **Files likely touched**
  - `orchestrator/routes/signals.py` (lines around 444–465).
  - `orchestrator/services/signal_source_detection.py` (new).
  - `orchestrator/tests/test_routes_no_adapter_imports.py` (new).
  - `orchestrator/tests/test_vendored_models_in_sync.py` (new).
  - `orchestrator/tests/test_capability_manifest_validation.py` (new).
- **Tests required**
  - All new tests above pass.
  - `make test`, `make compose-test`, and `make web-test` are unchanged.
  - Existing signal route tests pass against the refactored helper.
- **Rollback risk**
  - Low. The signal-detection refactor preserves the public route signature; if the new tests catch a layering violation in shipped code, the fix is local to that route or the allow-list comment.
- **Acceptance criteria**
  - Phase 1 exit criteria 1, 2, 3, 5, 6 are met (B2, B7, C7 closed at the CI level).

### Chunk 3 — Tool catalog design and read-only implementation

**Suggested slug:** `unified_tool_catalog`.

- **Scope**
  - Write a **short** design doc inside the implementation plan describing the catalog (sources, fields, per-user filtering, rendering for LLM vs MCP vs Web).
  - Implement `services/unified_tools.py` (or `execution/catalog.py`) as a **read-only** view that observes the live registries and returns an in-memory snapshot.
  - Add the four catalog tests listed in §3 Phase 2 exit criteria.
  - Add the contributor doc `docs/architecture/tool-vocabulary.md` (if not landed in Chunk 1).
- **Explicit non-goals**
  - **No** change to `loop.py`, `services/tools.run_tool`, `mcp_server.py`, or capability execution paths.
  - No new database table.
  - No new transport.
  - No new LLM behaviour.
- **Files likely touched**
  - `orchestrator/services/unified_tools.py` (new).
  - `orchestrator/tests/test_tool_catalog_includes_core_tools.py` (new).
  - `orchestrator/tests/test_tool_catalog_includes_plugin_tools.py` (new).
  - `orchestrator/tests/test_tool_catalog_mcp_vs_llm.py` (new).
  - `orchestrator/tests/test_tool_catalog_capability_discovery.py` (new — uses `httpx.MockTransport`).
  - `docs/architecture/tool-vocabulary.md` (new or extended).
- **Tests required**
  - The four new tests pass.
  - All existing tool / loop / MCP tests pass with no behaviour change (`test_loop_*`, `test_mcp_tools.py`, `test_query_graph_proxy.py`, `test_run_tool_requires_user_id`).
- **Rollback risk**
  - Very low. The catalog is read-only; nothing else consumes it yet.
- **Acceptance criteria**
  - Phase 2 exit criteria 1–6 met. **Done 2026-04-25** (see Phase 2 header note for Chunk 3).

### Chunk 4 — Minimal execution bridge

**Suggested slug:** `unified_tool_execution_plane` (Phase 3 critical path; spans multiple PRs).

- **Scope**
  - Generalise `register_query_graph_proxy` into a reusable `CapabilityHttpToolProxy` (suggested location: `services/unified_tools.py`).
  - Add a `ToolExecutor` that wraps `permissions.check_permission` (Ask/Do) and emits an `AuditEnvelope` (correlation id + `user_id` + `tool_name` + `capability_id`) regardless of whether the tool is in-process, plugin, or capability-backed.
  - Add a **mock capability service** in tests that publishes one tool through `httpx.MockTransport`; assert (a) the executor invokes it, (b) it fails closed when the mock service is unhealthy or unauthorised, (c) the audit envelope is emitted.
- **Explicit non-goals**
  - **No** change to which tools the LLM tool loop renders **yet** (that's Chunk 5).
  - No mTLS, no signed manifests, no marketplace.
  - No async migration.
- **Files likely touched**
  - `orchestrator/services/unified_tools.py` (extended).
  - `orchestrator/services/tools.py` (extract the graph proxy onto the new abstraction; preserve `register_query_graph_proxy` as a thin wrapper for backwards compatibility).
  - `orchestrator/tests/test_capability_http_tool_proxy.py` (new, mock-transport).
  - `orchestrator/tests/test_tool_executor_audit.py` (new).
  - `orchestrator/tests/test_tool_executor_permission_check.py` (new).
- **Tests required**
  - The three new tests pass.
  - `test_query_graph_proxy.py` continues to pass (graph parity preserved).
  - `make compose-test` and `test-graph-parity` (the slow, opt-in Make target) pass.
- **Rollback risk**
  - Medium. The graph proxy refactor must preserve byte-identical behaviour for existing graph clients. Mitigation: the existing schema-parity test `test_register_query_graph_proxy_schema_matches_plugin` already enforces this; the chunk's first commit moves the graph proxy onto the new abstraction with **no** behaviour change.
- **Acceptance criteria**
  - Phase 3A (execution bridge, graph on shared HTTP helper, executor/audit/permission test coverage) met **2026-04-25** — see §3 Phase 3 **Chunk 4** note. Remaining Phase 3 **full** exit criteria (Chunk 5+).

### Chunk 5 — Wire LLM / MCP views

**Suggested slug:** `llm_loop_capability_tools_wiring` (Phase 3 critical path, completes Chunk 4).

- **Scope**
  - Wire `loop.ask` / `ask_stream` to consume the tool catalog (option (a) or (b) from §3 Phase 3 step 3, picked by the implementation plan).
  - Decide and document the MCP path (option (a) or (b) from §3 Phase 3 step 5). If MCP shares the catalog, regenerate `MCP_TOOLS_FOR_MANIFEST` from the catalog and confirm the public manifest at `GET /capabilities` is byte-identical for the five existing tools. If MCP keeps its own list, ensure the catalog-consistency test from Chunk 3 documents the divergence.
  - Verify graceful degradation: when no capability is healthy, the LLM `tools=` array is byte-identical to today.
- **Explicit non-goals**
  - **No** new capability service in production.
  - **No** removal of the legacy `register_query_graph_proxy` symbol (kept as a thin wrapper).
- **Files likely touched**
  - `orchestrator/loop.py`.
  - `orchestrator/services/tools.py`.
  - `orchestrator/mcp_server.py` (only if option (a) chosen for MCP).
  - `orchestrator/tests/test_loop_uses_unified_catalog.py` (new).
  - `orchestrator/tests/test_mcp_manifest_unchanged.py` (new — byte-compare regression test against `routes/capabilities.py`'s response).
- **Tests required**
  - All new tests pass.
  - Existing `test_loop_*`, `test_mcp_tools.py`, `test_chat_completions_*` tests pass.
  - `test_register_query_graph_proxy_schema_matches_plugin` passes.
- **Rollback risk**
  - Medium-high (chat hot path). Mitigation: the wiring lands behind a small env flag (e.g. `LUMOGIS_TOOL_CATALOG_ENABLED=true`) initially; flip the default to `true` in a follow-up commit once tests confirm parity. Remove the flag after one release.
- **Acceptance criteria**
  - Phase 3 exit criteria 5, 7 met (in-process tools byte-identical; MCP behaviour preserved or documented divergence verified).
- **Status (2026-04-25) — implemented**
  - **Feature flag:** `LUMOGIS_TOOL_CATALOG_ENABLED` (default **false**). When false, `loop.ask` / `ask_stream` pass the same `tools_mod.TOOLS` object to the provider as before; with the flag true but no eligible out-of-process capability (healthy + base URL + per-service `LUMOGIS_CAPABILITY_BEARER_*`), behaviour stays byte-identical.
  - **LLM view:** :func:`services.unified_tools.prepare_llm_tools_for_request` + :func:`services.unified_tools.finish_llm_tools_request` (ContextVar for OOP routes). ``loop`` wraps prepare in try/finally and fail-closed fallback to `TOOLS` on exception.
  - **Dispatch:** unknown tool names in :func:`services.tools.run_tool` may delegate to :func:`services.unified_tools.try_run_oop_capability_tool` (HTTP via :class:`services.execution.ToolExecutor` only for routes installed on that request). **MCP** surface unchanged; no migration of `MCP_TOOLS_FOR_MANIFEST` in this chunk.
  - **Audit:** injectable :class:`services.execution.ToolAuditEnvelope` in the executor; for the **OOP catalog bridge**, `try_run_oop_capability_tool` also calls :func:`services.execution.persist_tool_audit_envelope` → ``audit_log`` (`audit_log_oop_fanin`, 2026-04-26) alongside `oop_tool_audit` structlog.
  - **Tests added:** `test_loop_uses_unified_catalog.py`, `test_llm_capability_tool_dispatch.py`, `test_mcp_manifest_unchanged.py`.
- **Closeout / hardening (2026-04-25)**
  - **Isolation:** OOP tool routes live in a :class:`contextvars.ContextVar` (`OOP_TOOL_ROUTES`). They are set only by :func:`prepare_llm_tools_for_request` and cleared by :func:`finish_llm_tools_request` in ``loop``’s ``finally``. Without a prepared route, :func:`run_tool` does not invoke capability HTTP. Concurrent threads each hold an independent context (see `test_loop_oop_tool_isolation.py`).
  - **Streaming:** ``ask_stream`` uses the same ``try``/``finally`` cleanup as ``ask`` so the ContextVar resets after provider errors or early generator exit.
  - **Compose / pytest:** The orchestrator runtime image does **not** ship pytest. Use **`make compose-test`** (installs `orchestrator/requirements-dev.txt` in the container, then `python -m pytest`) or **`make test`** from the repo root with a local venv (`PYTHON` defaults to `python3` in the `Makefile` as of `dev_test_workflow_hardening`, 2026-04-26). A bare `docker compose run orchestrator pytest` is not a supported entrypoint.
  - **Tests added (closeout):** `test_loop_oop_tool_isolation.py`.

### Chunk 6 — Lumogis Web alignment

**Suggested slug:** `lumogis_web_capability_and_credential_facades` (Phase 4; **Dependencies** in §3 Phase 4 govern sequencing — e.g. LLM/notification/diagnostics facades can land before the Phase-2 catalog; `/api/v1/me/tools` waits on Phase 2+3).

- **Status — Phase 4 / `me_tools_catalog_facade` (2026-04-26)**
  - **Shipped:** `GET /api/v1/me/tools` (authenticated via existing ``/api/v1/me`` router). Read-only JSON from :func:`services.me_tools_catalog.build_me_tools_response` → :func:`services.unified_tools.build_tool_catalog_for_user`. No tool execution, no raw JSON Schema, no secrets. OpenAPI snapshot + `REQUIRED_V1_PATHS` updated; Web types regenerated locally via `npm run codegen` from the snapshot (generated `openapi.d.ts` remains gitignored).
- **Status — Phase 4 / `lumogis_web_tools_catalog_view` (2026-04-26)**
  - **Shipped (Web, UI-only):** Settings → **Tools & capabilities** at ``/me/tools-capabilities``. Uses :func:`fetchMeTools` (`clients/lumogis-web/src/api/meTools.ts`) → ``GET /api/v1/me/tools``. Read-only table + summary + light filters (source, availability). **Observational only** — no run/execute controls, no permission editing. Vitest coverage in ``MeToolsCapabilitiesView.test.tsx``.
- **Status — Phase 4 / `me_llm_providers_facade_and_view` (2026-04-26)**
  - **Shipped:** ``GET /api/v1/me/llm-providers`` on ``orchestrator/routes/me.py`` (existing ``/api/v1/me`` router). Service :func:`services.me_llm_providers.build_me_llm_providers_response` — LLM connector ids from :data:`services.llm_connector_map.LLM_CONNECTOR_BY_ENV` (aligned with ``connectors/registry.py``); metadata via :func:`connector_credentials.get_record`, :func:`credential_tiers.household_get_record`, :func:`credential_tiers.system_get_record`, and a boolean env-fallback probe (no env values in JSON). Pydantic DTOs ``MeLlmProvidersResponse`` / ``MeLlmProviderItem`` in ``models/api_v1.py``. OpenAPI snapshot + ``REQUIRED_V1_PATHS``; tests ``orchestrator/tests/test_api_v1_me_llm_providers.py``.
  - **Shipped (Web):** Settings → **LLM providers** at ``/me/llm-providers`` — :func:`fetchMeLlmProviders` (`clients/lumogis-web/src/api/meLlmProviders.ts`), read-only ``MeLlmProvidersView`` (replaces the placeholder that only embedded **Connectors**). Vitest: ``MeLlmProvidersView.test.tsx``. **No** secret fields, **no** credential forms on this page.
- **Status — Phase 4 / `me_notifications_facade_and_view` (2026-04-25)**
  - **Shipped:** ``GET /api/v1/me/notifications`` on ``orchestrator/routes/me.py``. Service :func:`services.me_notifications.build_me_notifications_response` maps **ntfy** (registry connector + tier walk consistent with runtime) and a synthetic **web_push** row (subscription count + VAPID env presence only). **Read-only** — no PUT/POST; does **not** decrypt credential blobs for display (encrypted tiers report URL/topic/token as unknown where applicable); legacy env fallback exposes deployment URL and booleans only, never topic/token values. Pydantic DTOs ``MeNotificationsResponse`` / ``MeNotificationChannelItem`` in ``models/api_v1.py``. OpenAPI snapshot + ``REQUIRED_V1_PATHS``; tests ``orchestrator/tests/test_api_v1_me_notifications.py``. **Does not** change :func:`services.ntfy_runtime.load_ntfy_runtime_config` or delivery paths.
  - **Shipped (Web):** Settings → **Notifications** at ``/me/notifications`` — :func:`fetchMeNotifications` (`clients/lumogis-web/src/api/meNotifications.ts`), read-only ``MeNotificationsView``. Copy points users to **Connectors** for ntfy credential edits. Vitest: ``MeNotificationsView.test.tsx``.
  - **Deferred (follow-up slug):** ``me_notifications_edit_facade_or_connector_link`` — any dedicated edit/update façade for notification targets remains out of scope; household operators continue to use the existing Connectors flow unless a later chunk adds a safe write path.
- **Status — Phase 4 / `admin_diagnostics_v1_facade` (2026-04-26)**
  - **Shipped:** ``GET /api/v1/admin/diagnostics`` on ``orchestrator/routes/admin_diagnostics.py`` (same router as ``credential-key-fingerprint``; ``Depends(require_admin)``). Service :func:`services.admin_diagnostics.build_admin_diagnostics_response` — Core flags (auth, tool-catalog flag, semver, MCP booleans), store ping rows (postgres, qdrant, embedder, optional graph), capability registry summary (sorted ids, health/version/tool counts, last-seen timestamps), tool catalog summary via :func:`services.me_tools_catalog.build_me_tools_response` for the **calling admin's** ``user_id`` (read-only; no execution), static safe warnings (e.g. codegen check). **Read-only** — no env dumps, no credential payloads, no new health probes beyond existing ``ping()`` calls. Does not alter ``GET /health``, capability execution, or tool routing.
  - **Shipped (Web):** Admin → **Diagnostics** at ``/admin/diagnostics`` — extended ``AdminDiagnosticsView`` with :func:`fetchAdminDiagnostics` (`clients/lumogis-web/src/api/adminDiagnostics.ts`); existing credential-key fingerprint table retained below. Vitest: ``AdminDiagnosticsView.test.tsx``.
  - **Note:** Legacy ``GET /health`` (root) and HTML admin dashboard remain; this façade is additive for Lumogis Web.

### Deferred follow-ups preserved across Phase 4 (not implemented in individual façade chunks)

These items are called out in [`self-hosted-remediation-consolidation-review.md`](self-hosted-remediation-consolidation-review.md) and related plan text; **the notifications and admin-diagnostics façade chunks do not close them**. Track via portfolio / future plans as appropriate.

| Slug | Notes |
| --- | --- |
| ~~`audit_log_oop_fanin`~~ | **Done (2026-04-26):** OOP path persists `tool.execute.capability` rows via `write_audit`; see Phase 5 plan FU-2. |
| ~~`tool_catalog_permission_resolution`~~ | **Done (2026-04-26):** FU-3 — `get_connector_mode` in unified catalog / `/api/v1/me/tools`. |
| `capability_invoke_contract_v1` | Formal HTTP invoke contract beyond graph-shaped conventions. |
| `tool_catalog_flag_rollout` | `LUMOGIS_TOOL_CATALOG_ENABLED` default-off ops/docs follow-up. |
| `kg_capability_auth_hardening` | KG / capability HTTP auth posture (optional bearer / legacy paths). |
| `openapi_check_offline_or_mock` | `npm run codegen:check` / `make web-codegen-check` expects a **live** orchestrator at `LUMOGIS_OPENAPI_URL` — offline or mock CI remains future work. |
| `dev_venv_documentation_hardening` / `dev_test_workflow_hardening` | **Addressed (2026-04-26):** `PYTHON ?= python3` for local `make test` targets; contributor docs for venv vs Docker (`make compose-test`); removed obsolete `make setup` references. |
| `me_llm_providers_runtime_decrypt_health` | LLM façade shows metadata only; runtime decrypt / provider health probes for display are **not** part of the LLM or notifications chunks. |
| `household_system_credential_admin_ux` | Household- vs system-scoped credential management UX beyond thin read-only facades. |
| `me_notifications_edit_facade_or_connector_link` | Optional future write façade or deep-link-only pattern for notification target edits. |

- **Scope**
  - Identify the missing `/api/v1/me/*` and `/api/v1/admin/*` thin facades Web actually needs (start from §3 Phase 4 step 4: LLM providers, notifications, tools, admin diagnostics).
  - Implement each facade as a small chunk; each should ship with a snapshot test extending `REQUIRED_V1_PATHS`.
  - Wire `/api/v1/me/tools` to the unified catalog; render in Web's Settings.
  - **Curated credential view** for LLM providers: returns connector id + label + last-used + active-tier; never returns ciphertext or plaintext.
- **Explicit non-goals**
  - **No** removal of legacy HTML admin routes.
  - **No** new Web framework or rewriting existing pages.
  - **No** mobile / offline / push work (separate cross-device plan).
- **Files likely touched**
  - `orchestrator/routes/api_v1/me/llm_providers.py` (new).
  - `orchestrator/routes/api_v1/me/notifications.py` (new).
  - **As shipped (2026-04-26):** `GET /api/v1/me/tools` is `GET /tools` on the existing `APIRouter(prefix="/api/v1/me")` in `orchestrator/routes/me.py` (not a separate `routes/api_v1/me/tools.py` module). Future facades may still split routers by file if maintainers prefer.
  - **As shipped:** admin diagnostics live on ``orchestrator/routes/admin_diagnostics.py`` (``GET /api/v1/admin/diagnostics`` + fingerprint), not under ``routes/api_v1/``.
  - `clients/lumogis-web/src/pages/Settings/Capabilities.tsx` (or equivalent — new view).
  - `clients/lumogis-web/openapi.snapshot.json` (regenerated).
  - `orchestrator/tests/test_api_v1_openapi_snapshot.py` (extend `REQUIRED_V1_PATHS`).
  - **As shipped:** ``orchestrator/tests/test_api_v1_me_tools.py``, ``test_api_v1_me_llm_providers.py``, ``test_api_v1_me_notifications.py``, ``test_api_v1_admin_diagnostics.py``, ``test_admin_diagnostics_routes.py`` (fingerprint).
- **Tests required**
  - All new route tests pass.
  - OpenAPI snapshot updated and `npm run codegen:check` is green.
  - `make web-test` passes.
- **Rollback risk**
  - Low (additive surfaces; legacy admin untouched).
- **Acceptance criteria**
  - Phase 4 exit criteria 1–6 met.

---

## 7. Follow-up Cursor prompts

These are ready-to-paste Cursor prompts. Each instructs Cursor to **inspect, plan, and modify only the intended scope**. They do **not** generate implementation code in advance; they ask for a `/create-plan`-shaped output that the human reviews before any runtime change lands.

### 7.1 Prompt for Phase 0 / Chunk 1 — audit vocabulary and backlog mapping

```
You are working on the Lumogis self-hosted family AI platform.

Source materials (read first, in this order):
- docs/architecture/lumogis-self-hosted-platform-remediation-plan.md (this plan;
  pay special attention to §3 Phase 0, §4 reprioritised backlog, §6 Chunk 1).
- docs/private/LUMOGIS-ARCHITECTURE-AUDIT.md (preserve verbatim — do not edit).
- ARCHITECTURE.md (line 335 — the plugin import allow-list sentence).
- docs/decisions/005-plugin-boundary.md.
- orchestrator/plugins/graph/__init__.py (line 21 — confirms the contradiction).
- orchestrator/routes/connector_permissions.py (lines 90–120 — the misleading
  "capability registry" wording).

Goal:
Produce a /create-plan-style implementation plan for the chunk slug
`phase_0_audit_vocabulary_and_backlog_mapping`. The plan must:

1. Be DOC- and STRING-RENAME-only. No change to runtime behaviour, no change
   to LLM tool execution, no change to capability discovery, no new tests
   that exercise capability HTTP transport.
2. Land these specific edits, no more:
   a. Update `ARCHITECTURE.md` line 335 so the plugin import allow-list
      reflects reality (the graph plugin imports `config`). Either replace
      with an explicit allow-list (`ports/`, `models/`, `events.py`,
      `hooks.py`, named `config` factories) or add a side-by-side
      `docs/architecture/plugin-imports.md` companion that ARCHITECTURE.md
      links to. Keep ADR 005 untouched.
   b. Rename the misleading log keys and the advisory header constant
      (`_ACTION_REGISTRY_UNAVAILABLE_HEADER`) in
      `orchestrator/routes/connector_permissions.py` so they say
      "action registry" (because `_known_connectors` reads
      `actions.registry.list_actions`, not `CapabilityRegistry`). *(Shipped in Chunk 1.)*
   c. Update any test that pins the old literal strings.
   d. (Optional, only if the maintainer opts in.) Land a short
      `docs/architecture/tool-vocabulary.md` glossary aligned with the §3
      Phase 0 terminology table.

3. Honour the workspace rules documented for Cursor skill workflows (maintainer-local rule files; **not** tracked in this repository). In particular:
     - Do NOT manually edit topic indexes, follow-up portfolio rows, or other skill-managed planning trees on maintainer checkouts, and do not hand-edit **`docs/decisions/`** outside the normal `/verify-plan` / `/record-retro` flow.
     - The plan is created via /create-plan; the topic index updates via
       the skill chain.

4. Produce the plan with:
     - explicit list of files touched,
     - explicit non-goals (no LLM behaviour change, no MCP change, no
       capability change, no schema migration, no Plugin SDK),
     - tests required (existing tests must pass; renamed assertions
       updated),
     - rollback risk (very low),
     - acceptance criteria mirroring §3 Phase 0 exit criteria 3 and 4 in
       this document.

5. Do NOT write the implementation in this prompt. Output the plan only.
```

### 7.2 Prompt for Phase 1 / Chunk 2 — boundary hygiene

```
You are working on the Lumogis self-hosted family AI platform.

Source materials (read first):
- docs/architecture/lumogis-self-hosted-platform-remediation-plan.md
  (especially §3 Phase 1 and §6 Chunk 2).
- docs/private/LUMOGIS-ARCHITECTURE-AUDIT.md (rows B2, B3, B7, C2, C7,
  C8 — already prioritised P0 in this remediation plan).
- ARCHITECTURE.md §"Dependency direction" (the rule that routes must not
  import adapters).
- orchestrator/routes/signals.py (lines 440–465 — the `_detect_source`
  helper that imports adapters).
- orchestrator/services/capability_registry.py (lines 51–56 — the
  deliberate jsonschema non-goal that this chunk does NOT remove,
  but that this chunk DOES backstop with a CI fixture).
- Makefile (lines around 94–110 — the `sync-vendored` target loops over
  both `webhook` and `capability`).
- docs/decisions/011-lumogis-graph-service-extraction.md (the canonical
  KG model contract).

Goal:
Produce a /create-plan-style implementation plan for the chunk slug
`architecture_import_boundary_tests`. The plan must:

1. Refactor `orchestrator/routes/signals.py::_detect_source` so the route
   no longer imports `adapters.rss_source.RSSSource` or
   `adapters.page_scraper.PageScraper`. Move detection into a service
   helper (e.g. `orchestrator/services/signal_source_detection.py`)
   that owns the adapter imports. Preserve the route's public behaviour
   byte-for-byte.

2. Add `orchestrator/tests/test_routes_no_adapter_imports.py`. The test
   must AST-scan every Python file under `orchestrator/routes/` and fail
   if any module imports a name from `adapters.*`. Allow targeted
   exemptions only via an in-test allow-list with a comment explaining
   why each exemption exists (target: the allow-list ships empty after
   this chunk's refactor).

3. Add a vendored-model drift CI gate. Two acceptable shapes:
   (a) A pytest under `tests/integration/` that runs
       `make sync-vendored` and asserts
       `git diff --name-only services/lumogis-graph/models/` is empty.
   (b) A Makefile recipe (`make vendored-check` or extend the existing
       gate) that does the same; CI invokes the recipe.
   The chunk picks one and explains why.

4. Add `orchestrator/tests/test_capability_manifest_validation.py` with
   one good-manifest fixture (asserts registry registers it) and one
   bad-manifest fixture (asserts the registry rejects it without crashing
   the lifespan). DO NOT remove the deliberate jsonschema non-goal in
   `services/capability_registry.py` — the CI fixture is sufficient at
   this stage.

5. Honour the same Cursor skill-management constraints as in §authoring — **no manual edits** to skill-managed files on maintainer-only workspaces.

6. Explicit non-goals (state these in the plan):
   - No change to LLM tool execution.
   - No change to capability discovery beyond fixture validation.
   - No removal of the legacy graph plugin (FP-031 stays open).
   - No Plugin SDK on PyPI.
   - No new env vars beyond what tests need.

7. Acceptance criteria (mirror §3 Phase 1 exit criteria 1, 2, 3, 5, 6
   in this document).

8. Do NOT write the implementation in this prompt. Output the plan only.
```

### 7.3 Prompt for Phase 2 / Chunk 3 — tool catalog design and read-only implementation

```
You are working on the Lumogis self-hosted family AI platform.

Source materials (read first):
- docs/architecture/lumogis-self-hosted-platform-remediation-plan.md
  (especially §3 Phase 2 and §6 Chunk 3).
- docs/private/LUMOGIS-ARCHITECTURE-AUDIT.md (rows B1, B4, C1, C3, C4,
  C10 — already prioritised P1 in this remediation plan; the catalog is
  the foundation that lets execution unification land in Chunks 4–5).
- ARCHITECTURE.md §"Five concepts" and §"Ecosystem plumbing" (preserve
  the five-pillar model; the catalog is an OVERLAY, not a sixth pillar).
- orchestrator/services/tools.py (current `TOOL_SPECS`, `TOOLS`, and the
  `_add_plugin_tool` listener at lines 354–361).
- orchestrator/services/capability_registry.py (`get_tools()` and
  `RegisteredService` shape).
- orchestrator/loop.py (line 19 — `from services.tools import TOOLS`;
  this chunk does NOT change loop.py).
- orchestrator/mcp_server.py (`MCP_TOOLS_FOR_MANIFEST` — the MCP-visible
  tool list).
- orchestrator/plugins/graph/__init__.py (the in-process tool-registration
  pattern via `Event.TOOL_REGISTERED`).
- orchestrator/services/tools.py — `register_query_graph_proxy` (the
  proxy ToolSpec for `GRAPH_MODE=service`; this chunk represents it in
  the catalog but does NOT generalise it yet — that is Chunk 4).

Goal:
Produce a /create-plan-style implementation plan for the chunk slug
`unified_tool_catalog`. The plan must:

1. Implement `orchestrator/services/unified_tools.py` (or
   `orchestrator/execution/catalog.py`) as a READ-ONLY view that observes
   the live registries and returns an in-memory snapshot per call. The
   catalog must answer:
     - what tools exist,
     - where they came from
       (`core | plugin | mcp | capability:<id> | proxy:<id>`),
     - which transport can expose them
       (`llm_loop | mcp_surface | both | catalog_only`),
     - which connector / action / permission governs them,
     - whether they are available for a given user
       (combining `connector_permissions`, capability health, and
       credential resolution),
     - which origin tier they belong to
       (`local | plugin | mcp_only | capability_backed`).

2. Add four tests:
   a. `tests/test_tool_catalog_includes_core_tools.py` — every name in
      `TOOL_SPECS` appears in the catalog.
   b. `tests/test_tool_catalog_includes_plugin_tools.py` — a plugin
      tool (use the in-process graph plugin in `inprocess` mode, or a
      stub) appears with `source=plugin`.
   c. `tests/test_tool_catalog_mcp_vs_llm.py` — compares MCP-visible
      and LLM-visible sets and EXPLICITLY enumerates intentional
      differences (e.g. `memory.search` is MCP-only by design per
      ADR 010).
   d. `tests/test_tool_catalog_capability_discovery.py` — uses
      `httpx.MockTransport` to register a mock capability; asserts the
      catalog reports it as `source=capability:<id>` with
      `transport=catalog_only` (i.e. discovered, not yet executable —
      execution wiring lands in Chunks 4–5).

3. Add (or extend) `docs/architecture/tool-vocabulary.md` defining
   Tool, Action, Connector, Capability, Plugin, Signal, Routine,
   MCP surface — aligned with §3 Phase 0 of this remediation plan.

4. Honour Cursor skill-management constraints — **no manual edits** to skill-managed files on maintainer-only workspaces.

5. Explicit non-goals (state these in the plan):
   - DO NOT change `orchestrator/loop.py`.
   - DO NOT change `orchestrator/mcp_server.py`.
   - DO NOT change `orchestrator/services/tools.run_tool`.
   - DO NOT add a new database table or schema migration.
   - DO NOT introduce a new transport (gRPC, WebSocket, etc.).
   - DO NOT generalise the graph proxy yet (that is Chunk 4).
   - DO NOT build a marketplace, signed manifest format, or Plugin SDK.

6. Acceptance criteria (mirror §3 Phase 2 exit criteria 1–6 in this
   document; in particular, `make compose-test` and the existing
   `test_loop_*`, `test_mcp_tools.py`, `test_query_graph_proxy.py`,
   `test_run_tool_requires_user_id` suites must remain green).

7. Do NOT write the implementation in this prompt. Output the plan only.
```

---

*End of remediation plan.*
