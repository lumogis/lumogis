Verification: **read-only** checks completed — `git status` clean on `dev`; findings come from codebase and docs searches (no test run required for this audit).

---

# Lumogis Agent Harness Foundation Audit

**Related decision:** `docs/decisions/034-agent-harness-foundation-terminology-and-boundaries.md` (terminology and boundaries).

## 1. Executive summary

Lumogis already implements a **thick deterministic core** around tools, permissions, and audit: a bounded **LLM tool loop** (`orchestrator/loop.py`), **in-process `ToolSpec` registration** plus **plugin discovery** (`orchestrator/services/tools.py`, `Event.TOOL_REGISTERED`), a **read-only unified tool catalog** (`orchestrator/services/unified_tools.py`) with a **user-scoped façade** (`GET /api/v1/me/tools`, `orchestrator/services/me_tools_catalog.py`, `orchestrator/routes/me.py`), **Ask/Do connector policy** (`orchestrator/permissions.py`, ADR-006 in `docs/decisions/006-ask-do-safety-model.md`), **action execution with audit** (`orchestrator/actions/executor.py`, `orchestrator/actions/audit.py`), **out-of-process capabilities** (`orchestrator/services/capability_registry.py`, `orchestrator/services/execution.py`), and a **separate MCP interoperability surface** (`orchestrator/mcp_server.py`) with intentional divergence from the LLM catalog (`docs/architecture/tool-vocabulary.md`). **Operator diagnostics** exist (`orchestrator/services/admin_diagnostics.py`, `orchestrator/routes/admin_diagnostics.py`). **Hooks** exist (`orchestrator/hooks.py`, `orchestrator/events.py`).

What is **not** present as a single product primitive is a **replayable session timeline** (server-side chat is intentionally not the memory system; client threads live in `clients/lumogis-web/src/features/chat/threadStore.ts`), a unified **ActionProposal** lifecycle object spanning tools + LLM + Web (actions and review flows exist but are not one DTO), and a formal **ContextBuilder v1** with inspection/token budget reporting (partial: `_inject_context` in `orchestrator/routes/chat.py`, memory/search services, MCP `context.build` in `mcp_server.py`). **Cursor skills** are **maintainer/devtools workflow**, not runtime Lumogis “skills” (`AGENTS.md` routes to `.cursor/skills`).

**Direction:** **Consolidate and name** what exists; **avoid** importing a coding-agent harness wholesale. Align future “Agentic Core” (`docs/architecture/agentic_core.md`) with these primitives rather than duplicating them.

**Coding agents (Cursor, ChatGPT, Claude):** for repo workflow, Product OS, and architecture orientation—read **`AGENTS.md`** and **`docs/LUMOGIS_CONTEXT_PACK.md`** first (canonical summary; refresh with **`/update-context-pack`**). This audit is product harness evidence, not a substitute for that onboarding.

## 2. Key conclusion

**We need consolidation of existing Lumogis primitives and explicit boundaries (ADRs, vocabulary), not a new parallel architecture.** The “agent harness” pattern maps reasonably onto **loop + ToolSpec/catalog + permissions + actions/audit + capability/MCP layers** already in the repo. Gaps are mainly **unified event/timeline**, **inspectable context assembly**, and **product-level narrative**—not a greenfield engine.

## 3. Existing implementation map

| Agent-harness concept | Existing Lumogis equivalent | Files / modules (representative) | Maturity | Fit with product story | Duplication / drift risk | Recommended action |
|----------------------|----------------------------|----------------------------------|----------|-------------------------|--------------------------|-------------------|
| **Agent loop** | Bounded tool-calling loop over `LLMProvider` | `orchestrator/loop.py`; `orchestrator/config.py` (`get_llm_provider`); `orchestrator/adapters/*` | **Working** | Strong: local-first, explicit rounds (`MAX_TOOL_ROUNDS`) | **Partial:** catalog extension via `prepare_llm_tools_for_request` vs static `TOOLS` | Document loop invariants; keep rounds/budget explicit in ADR |
| **ContextBuilder** | Memory/graph injection + hooks + MCP `context.build` | `orchestrator/routes/chat.py` (`_inject_context`); `orchestrator/plugins/graph/__init__.py` (`CONTEXT_BUILDING`); `orchestrator/services/memory/*`; `orchestrator/services/context_budget.py`; `mcp_server.py` | **Partial** | Strong for household KB | **Drift:** chat injection vs MCP context tool vs future Agentic Core | Unify **contract** (sources list, budget), not necessarily one module yet |
| **ToolCatalog** | `build_tool_catalog`, `ToolCatalogEntry`, `GET /api/v1/me/tools` | `orchestrator/services/unified_tools.py`; `orchestrator/services/me_tools_catalog.py`; `docs/architecture/tool-vocabulary.md` | **Working** | Strong: observability without execution | **MCP vs LLM list** intentional; risk if docs slip | Keep divergence explicit; optional later “approved subset” mapping |
| **PermissionEngine** | Ask/Do + `check_permission`; `ToolExecutor` permission | `orchestrator/permissions.py`; `orchestrator/services/execution.py`; ADR-006 | **Mature** (for actions/tools) | Core trust story | vs **risk tiers** (`services/api_v1_risk.py`)—ensure one mental model | Formalise “policy matrix” doc, not second checker |
| **ActionProposal** | Action specs + pending review for Ask mode; not one DTO | `orchestrator/actions/executor.py`; `orchestrator/routes/api_v1/approvals.py`; metadata store pending queries | **Partial** | Good for approvals | **Naming gap:** “proposal” vs `ActionResult` / review rows | Define canonical lifecycle in ADR when unifying UX |
| **SessionTimeline** | Client `sessionStorage` threads; DB logs, no unified replay stream | `clients/lumogis-web/src/features/chat/threadStore.ts`; `action_log` / `audit_log` (Postgres) | **None** (unified timeline) | Fits explainability **if** privacy-preserving | Duplicating full prompts in DB is a **privacy** risk | Defer server timeline until requirements clear; design event schema first |
| **AuditLog** | `audit_log`, `action_log`, tool audit envelopes | `orchestrator/actions/audit.py`; `orchestrator/services/execution.py` (`ToolAuditEnvelope`); ADR-019 | **Working** | Strong | OOP vs in-process coverage differs | Continue fan-in patterns; document coverage matrix |
| **SkillRegistry** | **Not** runtime—Cursor skills + `AGENTS.md` | `AGENTS.md`; `.cursor/skills/*` (via devtools symlink) | **Mature** (devtools), **none** (product runtime) | Devtools fit; **wrong** to clone as in-app “coding skills” | None if kept separate | Keep skills as **instruction packs** for builders; Agentic Core uses `AgentSpec` planning only (`docs/architecture/agentic_core.md`) |
| **Hook system** | `hooks.register` / `fire` + `events.Event` | `orchestrator/hooks.py`; `orchestrator/events.py`; plugin registration | **Working** | Good extension point | Ad-hoc vs typed streams | Gradual **typed payloads** doc; avoid arbitrary scripts in Core |
| **CapabilityRegistry** | Manifest discovery + health | `orchestrator/services/capability_registry.py`; `models/capability.py` | **Working** | Core authority story | Overlap with tool catalog sources | Already cross-linked in `unified_tools`—preserve |
| **MCP exposure layer** | Streamable HTTP, curated tools, JWT/`MCP_DEFAULT_USER_ID` | `orchestrator/mcp_server.py`; ADR-017 `docs/decisions/017-mcp-token-user-map.md` | **Working** | Interop, not backbone | **By design** not equal to LLM catalog | Keep MCP as **external**; align policies via ToolExecutor/permissions over time |
| **ProviderRouter** | `get_llm_provider`, model config, per-user keys | `orchestrator/config.py`; `docs/decisions/026-llm-provider-keys-per-user.md` | **Working** | Strong | N/A | Maintain single routing API |
| **Diagnostics / Doctor** | Admin diagnostics + store pings + tool summary | `orchestrator/services/admin_diagnostics.py`; `orchestrator/routes/admin_diagnostics.py` | **Partial → working** | Self-host operators | vs LibreChat/Caddy health elsewhere | Extend checks incrementally; link from runbooks |
| **Human approval UX** | Web approvals API + pending lists | `orchestrator/routes/api_v1/approvals.py`; `clients/lumogis-web` (features) | **Working** | Strong | Admin `review_queue` is **entity/dedup** not action inbox (`orchestrator/routes/admin.py`) | Clarify **two queues** in UX copy and ADR |
| **Memory write / extraction** | Ingestion pipeline, signals, graph plugin | `orchestrator/plugins/graph/`; ingestion docs in ADRs 013, 014; **not** chat-as-memory per `threadStore.ts` comments | **Working** (substrate) | Aligned | Risk if chat logged server-side without intent | Keep “memory = KG + Qdrant + entities” narrative |
| **Entity/KG** | Graph plugin, optional service mode | ADR-007, 011; `services/tools.py` (`query_graph`); tests `orchestrator/tests/test_graph_query.py` | **Working** | Core differentiator | N/A | Preserve; document in context contract |
| **Admin/user tool visibility** | `/me/tools` read model + Web settings | `MeToolsCapabilitiesView.tsx`; tests under `clients/lumogis-web/tests/features/me/` | **Working** | Trust/transparency | N/A | Optional richer **unavailable reasons** (already FP-051 in portfolio) |
| **CLI/dev workflow** | Skills, compose, Makefile, AGENTS | `AGENTS.md`; repo scripts | **Mature** | Maintainer trust | None | No product clone of “CLI agent” |
| **Linear / portfolio** | Linear OS + legacy FP register | `.cursor/follow-up-portfolio.md` (devtools; symlink from product checkout) | **Working process** | Governance | Dual tracking—per portfolio rules | New work → **Linear**; portfolio for closure evidence only |

## 4. Fit with Lumogis product story

- **Fits well:** Ask/Do, append-only audit, capability isolation, read-only tool catalog for transparency, MCP as **infrastructure exit** for external agents, admin diagnostics for self-hosters, local-first routing to Ollama/external providers, memory/graph as **household knowledge** (not chat logs).

- **Does not fit / reject for product:** Treating Cursor **skills** as a runtime clone of “Claude Code skills” inside Lumogis; **server-side full conversation replay** without a privacy-by-design event model; **forcing MCP** to be the internal tool bus (docs already say otherwise in `tool-vocabulary.md` and `mcp_server.py`).

- **Defer:** Unified **SessionTimeline** until Agentic Core phase and consent model are clear (`docs/architecture/agentic_core.md` already says do not implement yet). **Formal ActionProposal DTO** can wait until Web + Core need one inbox metaphor for both tools and actions.

## 5. Gap analysis

| Gap | Category | Severity | Notes |
|-----|----------|----------|--------|
| No **single replayable SessionTimeline** (model/tool/approval events) | Architecture + Product | **P2** (P1 when Agentic Core starts) | Client chat is deliberate ephemeral storage |
| **Context** assembly split across chat injection, MCP, memory | Architecture + Documentation | **P2** | Reduce conceptual fragmentation via ADR |
| **ActionProposal** as one lifecycle object | Architecture | **P2** | Actions today are real; unification is refinement |
| **Permission vs risk tier** narrative split (`permissions` vs `api_v1_risk`) | Naming / Documentation | **P2** | One policy story for users |
| **MCP tool list ≠ LLM catalog** — ops confusion | Documentation | **P2** | Already tested; needs operator-facing clarity |
| **Server-side explainability** for a single chat turn (tool args/results) | UX + Product | **P2** | Could improve trust without full timeline |
| **Portfolio FP items** (MCP, KG, permissions) already track related work | Roadmap | — | Link new ADR to **FP-019**, **FP-016**, **FP-048—FP-051** etc., not duplicate |
| **Test gap** on cross-surface policy alignment | Test | **P3** | Add when MCP/catalog alignment project runs |

## 6. Recommended architecture principles

*(Refined from repo evidence.)*

1. **`build_tool_catalog()` + `ToolSpec` execution are the internal truth** for “what exists” and “what the LLM path can use”; the catalog **observes** registries; it does not replace execution (`docs/architecture/tool-vocabulary.md`).

2. **MCP is an external interoperability layer** — stateless, curated tool list, user resolution via JWT / `MCP_DEFAULT_USER_ID` (`mcp_server.py`); **not** the spine of in-process orchestration.

3. **Ask/Do + `check_permission()` are the primary safety gate** for connector-scoped behavior (ADR-006); **risk tiers** supplement explainability/elevation, not a second permission silo.

4. **Actions + `audit_log` / `action_log` + `ToolAuditEnvelope`** are the audit backbone; extend **coverage** rather than replacing with a new log.

5. **Hooks (`hooks.py`) are synchronous extension points** with string events (`events.py`); evolve toward **documented payloads**, not arbitrary “user scripts in Core.”

6. **Context for chat** is assembled in **`_inject_context`** plus plugins (`CONTEXT_BUILDING`); **inspectability** (budget, sources) is a **product** feature for self-hosters when you formalise “ContextBuilder v1.”

7. **Skills** in this repo mean **Cursor/devtools instruction packs** (`AGENTS.md`); **runtime** “agents” follow **Agentic Core** concepts when that program starts—not a coding-agent harness.

8. **Diagnostics** (`admin_diagnostics`) are a **product operator surface**; expand them rather than relying only on developer scripts.

## 7. Proposed roadmap: Agent Harness Foundation

Phases **aligned to findings** (not all need separate Linear issues):

| Phase | Focus | Scope boundary |
|-------|--------|----------------|
| **A** | Audit consolidation + ADR | Terminology, mapping table in `docs/decisions/`, no code churn |
| **B** | ToolCatalog consolidation | **Document** `ToolSpec` / catalog / MCP rows; optional `ToolDescriptor` doc only if codegen needs it—**no** new DB |
| **C** | Permission / Ask-Do hardening | Single policy narrative; approval-required matrix in docs + tests for edge cases |
| **D** | ActionProposal lifecycle | **Only** when unifying pending actions + tool outcomes in one UX—may trail Agentic Core |
| **E** | SessionTimeline | **Defer** until Agentic Core; design privacy-minimal event schema first |
| **F** | ContextBuilder v1 | Deterministic source list + token budget reporting; wire into diagnostics optionally |
| **G** | MCP alignment | Policy alignment with ToolExecutor; **never** auto-mirror full internal catalog |
| **H** | Diagnostics | Extend `build_admin_diagnostics_response` with permission sanity / catalog health checks |
| **I** | Skills / workflow | **Maintain** devtools skills; no new “Lumogis runtime skills” unless Agentic Core ships |

## 8. Proposed Linear items

*Only items with real gaps; several numbered suggestions from the prompt are **already done** or **tracked**—do not open duplicates.*

---

**Title:** ADR: Agent Harness terminology & boundaries (Lumogis vs coding-agent harness)
**Type:** Architecture / Docs
**Priority:** P1
**Phase:** A
**Problem statement:** Conceptual overlap with “agent harness” language risks duplicate abstractions or MCP-as-spine mistakes.
**Proposed scope:** One ADR in `docs/decisions/` mapping loop, catalog, permissions, MCP, hooks, context, audit; explicit non-goals.
**Out of scope:** Implementation refactors, Agentic Core runtime.
**Acceptance criteria:** ADR merged; references `tool-vocabulary.md`, ADR-006, ADR-017, `agentic_core.md`.
**Dependencies:** None.
**Files:** `docs/decisions/NNN-*.md`, cross-links from `ARCHITECTURE.md`.
**Verification:** Doc review; no pytest requirement.
**Documentation:** ADR itself.
**Risks:** Over-long ADR—keep bounded.

---

**Title:** Context assembly contract (ContextBuilder v1 design)
**Type:** Architecture
**Priority:** P2
**Phase:** F
**Problem statement:** Context is built in multiple places (`_inject_context`, MCP, memory)—hard to explain and test as one system.
**Proposed scope:** Design doc: ordered sources, token budget, exclusion/inclusion reporting API (may be read-only first).
**Out of scope:** Full implementation in one PR.
**Acceptance criteria:** Approved design + checklist for future implementation chunks.
**Dependencies:** ADR from Phase A helpful.
**Files:** Likely `docs/architecture/` + later `orchestrator/routes/chat.py`, `services/context_budget.py`.
**Verification:** Design review.
**Risks:** Scope creep into Agentic Core—keep household-AI scoped.

---

**Title:** SessionTimeline / replay — requirements & privacy review
**Type:** Product / Security
**Priority:** P2 (elevate to P1 when Agentic Core is scheduled)
**Phase:** E
**Problem statement:** No unified replay stream; client chat is ephemeral by design.
**Proposed scope:** Requirements: which events, retention, per-user consent, PII boundaries; explicit comparison to `audit_log`.
**Out of scope:** Building storage.
**Acceptance criteria:** Signed-off mini-spec; ties to `agentic_core.md` `AgentRun` concept.
**Dependencies:** Product decision on whether chat transcripts belong server-side.
**Files:** Future `docs/decisions/`; **not** `threadStore.ts` without UX/legal clarity.
**Risks:** Privacy regression if full prompts stored.

---

**Title:** Extend admin diagnostics — permission & catalog sanity
**Type:** Backend / Product
**Priority:** P2
**Phase:** H
**Problem statement:** Operators need stronger **doctor** signals for misconfiguration (connectors, capability health, tool counts).
**Proposed scope:** Add read-only checks (e.g., zero LLM tools when flag on, capability unhealthy counts, Ask/Do unknowns)—extend `admin_diagnostics.py`.
**Out of scope:** Auto-remediation.
**Acceptance criteria:** New fields in DTO + tests in `test_api_v1_admin_diagnostics.py`.
**Dependencies:** None.
**Files:** `orchestrator/services/admin_diagnostics.py`, `orchestrator/models/api_v1.py`, tests.
**Verification:** `pytest` scoped tests.
**Risks:** False alarms—tune messaging.

---

**Title:** Reconcile MCP policy alignment with internal execution (roadmap chunk)
**Type:** Architecture / Backend
**Priority:** P2
**Phase:** G
**Problem statement:** **FP-019** already tracks MCP roadmap; alignment work should attach there or spawn a child issue.
**Proposed scope:** Child of **FP-019**: ensure capability tool calls fan into same audit patterns; document MCP vs catalog.
**Out of scope:** Full manifest rewrite.
**Acceptance criteria:** Linked to FP-019 closure criteria; tests touching OOP audit unchanged or improved.
**Dependencies:** Review existing FP-019 scope in Linear.
**Files:** `mcp_server.py`, `services/execution.py`, tests.
**Verification:** Existing compose/pytest paths for MCP/tool catalog.

---

**Items intentionally NOT created (already exist or tracked):**

- **Audit existing primitives** → this audit satisfies **discovery**.
- **verify-plan, linear-update, prepare-private-release, etc.** → `.cursor/skills/` already (`available_skills` list).
- **AGENTS.md** → exists at repo root (`AGENTS.md`).
- **Per-user tools façade / ToolCatalog Phase 2–4** → shipped (`unified_tools.py`, `me_tools_catalog.py`).
- **FP-051** — richer `/me/tools` unavailable reasons **already open** in portfolio—handle via Linear backfill to that row, not a duplicate issue.

## 9. Product roadmap / portfolio reconciliation

**Canonical execution backlog:** **Linear** (`LUM-*`) — stated explicitly in `follow-up-portfolio.md` (devtools). **Do not** add a second competing backlog doc in the product repo.

**Durable architecture record:** **`docs/decisions/`** (ADRs) for finalised decisions—**not** ad-hoc new roadmap files; `/create-plan` → `/verify-plan` pipeline per `AGENTS.md`.

**Where “Agent Harness Foundation” should live:**

- **Theme name:** Fold under **existing** directions: **self-hosted remediation / tool vocabulary** (`docs/architecture/lumogis-self-hosted-platform-remediation-plan.md`, `docs/architecture/tool-vocabulary.md`) and **future Agentic Core** (`docs/architecture/agentic_core.md`). A **single new ADR** (Phase A) should **tie these together** instead of a new top-level “Agent Harness” roadmap file (avoids **second source of truth**).

**Portfolio:** Existing rows **FP-019** (MCP), **FP-016** (connector permissions), **FP-048—FP-051** (capability scaffolding), **FP-044** (review_queue GET) already touch related surfaces—**link** new Linear issues as children or comments, **do not** duplicate in FP table (skill-managed file).

**Outdated / fragmented risk:** `docs/architecture/agentic_core.md` states **draft ADR none in repo** in header—may need **refresh** when ADR lands (cosmetic + cross-links only).

## 10. Risks and open questions

1. **Naming collision:** “Skills,” “agents,” “tools” mean different things in Cursor vs Lumogis vs MCP—ADR must nail glossaries.

2. **Privacy:** Server SessionTimeline vs “memory is KG+Qdrant+files” story—**must not** silently equate chat logs with memory.

3. **Operational complexity:** Making MCP mirror the full internal catalog would **increase** attack surface and confusion—**reject**.

4. **Agentic Core timing:** `agentic_core.md` blocks implementation until voice/capture milestones—harness consolidation **must not** fork a second runtime before that.

## 11. Recommended next implementation chunk

**Smallest high-value step:** Land **one ADR (Phase A)** that locks **terminology and boundaries** (internal ToolCatalog vs MCP vs execution vs Ask/Do) and references **existing** modules—**no code changes required** for value. Optional follow-on: **one** small **admin diagnostics** enhancement (Phase H) for operator-visible catalog/permission warnings.

---

**Tests:** Not run for this read-only audit (appropriate). **Commands:** `git status` succeeded; working tree clean.
