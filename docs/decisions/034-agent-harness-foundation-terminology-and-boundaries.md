# ADR 034: Agent Harness Foundation — terminology, boundaries, and existing Lumogis primitives

**Status:** Accepted  
**Created:** 2026-05-06  
**Last updated:** 2026-05-06  
**Decided by:** Architecture documentation (Phase A consolidation; roadmap linkage via **Linear** backlog and **`docs/LUMOGIS_REFERENCE_MANUAL.md`** §17).

## Context

Industry “agent harness” discussions often use coding-agent products as the reference shape (thin model loop, thick deterministic shell, tool catalog, permission engine, replay timelines). That comparison is useful for vocabulary, but it creates **risk** for Lumogis if product and engineering import **coding-agent assumptions** (IDE-centric workflows, repo-root autonomy, shell-first execution) that conflict with Lumogis’s goals.

**Lumogis is not building a Claude Code clone.** It is a **local-first, privacy-first, self-hosted household AI operating layer** — household knowledge, memory, search, tools, routines, and **user-approved** actions — not a coding agent.

Recent analysis (audit, 2026) compared those patterns to the codebase and concluded that Lumogis **already** has most of the relevant substrate (bounded LLM tool loop, `ToolSpec` registration, unified read-only tool catalog, Ask/Do, actions and audit, capabilities, MCP, hooks, diagnostics). The gap is **not** a greenfield “harness stack” but **explicit boundaries and shared terminology** so we **consolidate existing primitives** and avoid **duplicate abstractions** or an accidental second runtime.

## Decision

Lumogis adopts the following as the **canonical Agent Harness Foundation** — **naming the real system** that already exists or is deliberately deferred, and **anchoring** future work (including Agentic Core) to it:

| Concept | Role in the foundation |
|--------|-------------------------|
| **Agent loop** | Bounded LLM tool-calling loop over `LLMProvider` — the thin model reasoning slice. |
| **ToolSpec / ToolCatalog** | `ToolSpec` + execution path for callable tools; `ToolCatalog` as read-only observation of what exists and how it is surfaced. |
| **PermissionEngine / Ask–Do** | Connector-scoped permission checks (`check_permission`) and Ask vs Do modes — primary safety gate for tools and actions. |
| **Action execution and audit** | Registered actions, executor, append-only audit and action logs. |
| **CapabilityRegistry** | Discovery and health of out-of-process capability services. |
| **MCP exposure layer** | External interoperability for MCP clients; not the internal orchestration backbone. |
| **ContextBuilder contract** | Formal contract for how context is assembled (sources, budget, reporting) — to be refined; not required to be a single module immediately. |
| **Hooks / events** | Synchronous extension points (`hooks` + `Event` constants) for Core and plugins. |
| **Diagnostics** | Operator-facing read-only diagnostics (admin diagnostics, store checks, tool summaries). |
| **SessionTimeline** | **Deferred** until product and privacy requirements for replay/logging are agreed; no commitment to server-side full chat replay in this ADR. |
| **Skills** | **Devtools only** by default: Cursor instruction packs under `.cursor/skills/` and routing in `AGENTS.md`. A **future in-product** skill/runtime model requires a **separate ADR** — not implied here. |

**Agentic Core** (household-coordinated agents — backlog-gated product direction) **must build on** these primitives and policies — **not** introduce a parallel permission, tool, or audit subsystem.

## Canonical terminology

| Term | Meaning in Lumogis | Existing implementation / docs | Boundary / non-goal |
|------|--------------------|-------------------------------|----------------------|
| **Agent loop** | Bounded rounds of model completion + optional tool calls via `run_tool`. | `orchestrator/loop.py` | Not an unbounded agent runtime; round cap and provider config stay explicit. |
| **ToolSpec** | Registered tool definition (connector, action_type, schema, handler) used by the LLM path. | `orchestrator/services/tools.py`; plugin registration via `Event.TOOL_REGISTERED` | Not a duplicate “tool DTO” for every transport; execution stays in Core/plugin handlers + executor bridges. |
| **ToolCatalog** | Read-only, deterministic snapshot of tools/actions and transports (`build_tool_catalog`). | `orchestrator/services/unified_tools.py`; `docs/architecture/tool-vocabulary.md` | **Observes** registries; **does not** replace execution or grant permissions. |
| **`/me/tools`** | Authenticated façade over the catalog for Lumogis Web (`GET /api/v1/me/tools`). | `orchestrator/services/me_tools_catalog.py`; `orchestrator/routes/me.py` | Not execution, not credential surface, not permission grants. |
| **PermissionEngine** | **`permissions.check_permission`** + connector Ask/Do state — the primary gate for tool writes and actions. | `orchestrator/permissions.py`; `docs/decisions/006-ask-do-safety-model.md`; `orchestrator/services/execution.py` | Not replaced by risk-tier UI alone; tiers **explain** and support elevation, not a second silo. |
| **Ask / Do** | Per-connector mode: Ask blocks writes until approval/elevation; Do allows writes per policy. | ADR-006; `orchestrator/permissions.py` | Not fine-grained RBAC for every field; intentional trade-off per ADR-006. |
| **ActionProposal** | **Conceptual** pending write/request — today implemented via **actions**, review queues, and approvals APIs **without** a single unified DTO name in code. | `orchestrator/actions/executor.py`; `orchestrator/routes/api_v1/approvals.py` | A unified lifecycle type is **follow-up design**, not required by this ADR. |
| **AuditLog** | Append-only **audit_log** (and related **action_log** / tool audit envelopes). | `orchestrator/actions/audit.py`; `orchestrator/services/execution.py` (`ToolAuditEnvelope`) | Not replaced by a new logging system; extend coverage consistently. |
| **SessionTimeline** | **Deferred:** replayable ordered events (model, tools, approvals) for explainability. | Client chat persistence is intentionally limited (`clients/lumogis-web/src/features/chat/threadStore.ts` — documented as ephemeral); server timeline **not** mandated here | **No** server-side full chat replay without a **privacy-approved** follow-on. |
| **ContextBuilder** | **Contract** for deterministic, inspectable context (sources + token budget + inclusion/exclusion reporting). | `orchestrator/routes/chat.py` (`_inject_context`); `orchestrator/services/context_budget.py`; MCP `context.build` in `orchestrator/mcp_server.py`; `Event.CONTEXT_BUILDING` in `orchestrator/events.py` | **Formal design** (`ContextBuilder` v1) is follow-up; today’s code paths remain authoritative until then. |
| **CapabilityRegistry** | Discovered out-of-process services, manifests, health. | `orchestrator/services/capability_registry.py` | Capabilities **suggest** tools; Core **owns** policy and execution gates. |
| **MCP** | Streamable HTTP MCP surface for **external** clients — curated tool list, user resolution policy. | `orchestrator/mcp_server.py`; `docs/decisions/017-mcp-token-user-map.md` | **Not** a mirror of the full internal LLM tool list; not the internal message bus. |
| **Hook** | Synchronous callback registration for `Event` constants. | `orchestrator/hooks.py`; `orchestrator/events.py` | Not arbitrary end-user shell scripts in Core; evolve toward documented payloads. |
| **Skill** | **Maintainer/devtools:** instruction packs and workflows under `.cursor/skills/`; routing guidance in `AGENTS.md`. | Symlinked devtools cursor tree; `AGENTS.md` | **Not** Lumogis product runtime “skills” unless a future ADR defines them. |
| **Agentic Core** | Planned product direction for household-coordinated agents — **backlog-gated**; see **Linear** and release notes. | Linear / Product OS | Must not bypass Ask/Do, audit, or catalog authority; implementation order follows agreed chunks. |
| **Diagnostics / Doctor** | Read-only operator diagnostics (core flags, stores, capabilities, tool summary). | `orchestrator/services/admin_diagnostics.py`; `orchestrator/routes/admin_diagnostics.py` | Not auto-remediation; extend with checks in follow-up work. |

## Explicit boundaries

1. **MCP** is an **external interoperability layer**, not the internal backbone for Core orchestration.
2. **ToolCatalog** **observes and describes** tools; it **does not** replace **execution** (`run_tool`, `ToolExecutor`, handlers).
3. **ToolSpec** and the established **execution path** remain the **internal callable path** for LLM-invoked tools (plus controlled capability bridges).
4. **Ask/Do** and **`check_permission`** remain the **primary safety gate** for connector-scoped behaviour (ADR-006).
5. **Risk tiers** (e.g. elevation/eligibility UX) **support explanation and elevation**; they **must not** become a **second permission silo** that contradicts Ask/Do.
6. **`audit_log`**, **`action_log`**, and **`ToolAuditEnvelope`** remain the **audit backbone**; extend coverage rather than introducing a parallel audit product.
7. **ContextBuilder** is a **contract to formalise** in a later design — **not** necessarily one new module in the next change.
8. **SessionTimeline** is **deferred** and requires **explicit privacy and product review** before any server-side storage of conversational or tool payloads.
9. **Cursor skills** are **maintainer/devtools instruction packs** (`.cursor/skills/`), not Lumogis end-user runtime features unless a **separate ADR** says otherwise.
10. **Agentic Core** **builds on** these primitives — **no** parallel runtime that bypasses them.

## Existing implementation references

Representative files (audit-derived):

- `orchestrator/loop.py` — agent loop.
- `orchestrator/services/tools.py` — `ToolSpec`, `run_tool`.
- `orchestrator/services/unified_tools.py` — `build_tool_catalog`, LLM request preparation.
- `orchestrator/services/me_tools_catalog.py` — `GET /api/v1/me/tools` DTO builder.
- `orchestrator/routes/me.py` — me routes (tools façade).
- `orchestrator/permissions.py` — Ask/Do and `check_permission`.
- `docs/decisions/006-ask-do-safety-model.md` — Ask/Do ADR.
- `orchestrator/actions/executor.py` — action execution.
- `orchestrator/actions/audit.py` — audit writes.
- `orchestrator/services/capability_registry.py` — capability registry.
- `orchestrator/services/execution.py` — `ToolExecutor`, capability tool audit.
- `orchestrator/mcp_server.py` — MCP tools and mapping.
- `docs/architecture/tool-vocabulary.md` — catalog vs execution vocabulary.
- `orchestrator/hooks.py` — hook dispatch.
- `orchestrator/events.py` — event name constants.
- `orchestrator/services/admin_diagnostics.py` — diagnostics aggregation.
- `orchestrator/routes/admin_diagnostics.py` — admin diagnostics routes.
- `orchestrator/routes/chat.py` — chat routes, `_inject_context`.
- `orchestrator/services/context_budget.py` — context budget helpers.
- `AGENTS.md` — agent workflow router for repo work.
- `.cursor/skills/` — Cursor skills (see **`AGENTS.md`** for repo layout notes).

## Consequences

**Positive**

- Reduces duplicate architecture and **clarifies** the household AI product story vs coding agents.
- **Protects** privacy and trust boundaries (explicit deferral of SessionTimeline; no MCP-as-spine).
- **Scopes** MCP and catalog correctly for operators and implementers.
- Makes **Agentic Core** and Linear-sized chunks **easier to decompose** against stable names.

**Negative / trade-offs**

- **No immediate user-facing feature** — documentation and alignment only.
- **Gaps remain** (unified ActionProposal type, ContextBuilder v1, SessionTimeline) — tracked as follow-ups.
- **Context assembly** remains multi-path until a dedicated design lands.
- Requires **ongoing discipline** not to reintroduce “second permission” or “MCP-first internal bus” patterns.

## Non-goals

- No Claude Code clone and no Claw Code **dependency** or **source-code copying** from external products.
- No **arbitrary shell execution** for household users as part of this foundation.
- No **MCP-first internal bus** replacing Core tool execution.
- No **server-side full chat replay** without a **separate privacy decision**.
- No **in-product runtime skill system** in this ADR.
- **No product code changes** as part of this ADR.

## Follow-up work

| Follow-up | Description |
|-----------|-------------|
| **Context assembly contract / ContextBuilder v1** | Design ordered sources, token budget, inclusion/exclusion reporting; align chat, memory, and MCP context surfaces. |
| **SessionTimeline / replay** | Privacy and product requirements; event schema; retention; relation to `audit_log` — **before** implementation. |
| **ActionProposal lifecycle** | Unify naming and DTO only when Web + Core need a single pending-action metaphor. |
| **Admin diagnostics extension** | Stronger catalog/permission sanity signals for operators (`admin_diagnostics` family). |
| **MCP policy alignment** | Align external MCP behaviour with internal audit/permission expectations; track under **existing MCP roadmap / portfolio (e.g. FP-019)** — no second roadmap. |
| **Product roadmap / Linear reconciliation** | File follow-on **LUM-*** items as needed; **Linear** remains the active backlog per Product OS — this ADR does not create a parallel backlog. |

## Cross-links

- **`docs/LUMOGIS_REFERENCE_MANUAL.md`** — roadmap and shipped-vs-planned context.
- **`docs/architecture/tool-vocabulary.md`** — catalog vs execution vocabulary.
- **`docs/decisions/006-ask-do-safety-model.md`**, **`docs/decisions/017-mcp-token-user-map.md`**, **`docs/decisions/019-structured-audit-logging.md`** — related safety and audit decisions.

## Status history

- **2026-05-06:** Accepted — Phase A documentation consolidation (Agent Harness Foundation terminology and boundaries).
