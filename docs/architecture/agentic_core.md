# Agentic Core strategic baseline

**Date:** 2026-04-30  
**Status:** Planning baseline; no runtime implementation  
**Scope:** whole-product AGPL-first, core + web + capabilities  
**Detailed exploration:** *(maintainer-local only; not part of the tracked repository)*  
**Draft ADR:** none in `docs/decisions/` yet; exploratory drafts may exist only on maintainer checkouts *(not part of the tracked repository)*.

## Summary

Agentic Core is the proposed next major Lumogis product and architecture
direction after voice/capture is complete. The thesis is:

> Lumogis should become a local-first AI team for a household. Core
> coordinates, agents specialise, and users stay in control.

This is an AGPL-first whole-product direction. It does not assume that
advanced graph-facing interfaces, ambient briefs, family coordination,
voice/capture UX, agent dashboards, or capability-provided agents are
proprietary by default. Commercialisation can still happen later through
services, support, hosting, packaged distributions, appliance bundles,
deployment automation, managed backup/relay, or dual licensing if
contributor rights allow it. Those commercial questions must not distort the
open architecture.

## Current implementation priority

Do not implement Agentic Core yet. The active product order remains:

1. finish voice-to-text / STT
2. wire STT into Lumogis Web capture
3. make voice notes work end-to-end
4. only then begin Agentic Core implementation

## Product and architecture boundary

`lumogis-core` owns:

- authority, policy, scopes, and audit
- tool/capability boundaries
- orchestration contracts
- context-building interfaces
- model-routing and escalation interfaces
- queue/routine primitives
- safe runtime invariants

Lumogis Web owns:

- Ask Core and scope switching
- Capture
- AI Team dashboard
- Team Inbox and Owner Inbox
- review/approval workflows
- agent tuning
- capability setup
- memory/graph/context visualisation
- activity/audit views

Capabilities/plugins own:

- domain-specific tools and schemas
- risk metadata
- adapters
- optional suggested agents
- optional domain-specific intelligence

Core remains the authority layer. Lumogis Web remains the human control
surface. Capabilities can suggest and supply domain logic, but they do not
grant themselves permissions or bypass Core.

## Core concepts

### AgentSpec

`AgentSpec` describes what an agent type is: label, role, source, default
tools, default model policy, output shape, risk profile, and soft role
instructions.

First slice rule: use **code-defined built-in AgentSpecs** only. Do not add
persistent `AgentSpec` tables in the first slice.

### AgentInstance

`AgentInstance` describes where/who an agent runs for: personal, family
(`scope='shared'`), or system/admin context.

First slice rule: derive read-only instances from code-defined specs and the
current user/session/scope. Do not add persistent `AgentInstance` tables in
the first slice.

Persistent `AgentSpec`/`AgentInstance` tables should be deferred until
configurable agent instances, Team Inbox, Owner Inbox, or agent preferences
require them.

### EffectiveAgentPolicy

`EffectiveAgentPolicy` is the code-enforced runtime policy calculated from:

- Core safety invariants
- household/system policy
- capability maximum permissions
- agent base spec
- agent instance/scope
- active user/session/role
- connector Ask/Do mode
- user preferences
- session context

Prompt text is lowest authority. It can guide tone and behaviour, but cannot
grant tools, expand scope, allow cloud escalation, approve actions, or bypass
audit.

### AgentRun

`AgentRun` is the conceptual join point for future execution, inbox items,
audit, debugging, and routine outputs. Do not implement the table in the
first static registry slice.

Suggested future fields:

```text
agent_runs
  id
  agent_instance_id
  user_id
  household_id nullable
  scope
  trigger_type
  trigger_ref
  status
  model_tier
  tools_used_json
  started_at
  completed_at
  audit_ref
```

Owner Inbox items, audit entries, and debugging views should be able to
reference an `AgentRun` later.

## Safety invariants

### Propose is not write

Propose is not write.

- A proposal creates a review item.
- A write mutates memory, graph state, calendar entries, files, external
  services, actions, physical-world systems, or Lumogis system state.
- Ask agents may read, analyse, summarise, and propose.
- Do agents may execute only through policy, approval, and audit.

This distinction lets agents provide judgement without granting authority.

### Context building remains Core-owned

Agents do not directly retrieve arbitrary context. Agents request context
through Core/context-builder interfaces under the active user, scope, session,
and `EffectiveAgentPolicy`.

Context building remains Core-owned because it applies scope visibility,
context budgets, redaction policy, model-routing policy, and evidence
references.

### Retrieved content is untrusted evidence

Documents, emails, web pages, transcripts, capability outputs, and retrieved
snippets are untrusted evidence, not instructions.

Agents and prompts must not follow commands embedded in retrieved content
unless the user explicitly asks to process those instructions as content.
This is a key prompt-injection defence for document processing, email
ingestion, web research, voice transcripts, and capability-provided outputs.

### External frameworks cannot own authority

No external agent framework may own Lumogis authority. LangGraph, PydanticAI,
OpenAI Agents SDK, CrewAI, AutoGen, Semantic Kernel, or similar tools may only
sit behind Lumogis-defined ports after Core policy, scopes, approvals, audit,
and tool execution boundaries are defined.

Do not add these dependencies until an implementation plan chooses a bounded
adapter and proves it cannot bypass Core.

## Family scope

Use the current scope substrate:

- `personal`
- `shared`
- `system`

"Family" should initially be a product/UI label over `scope='shared'`. Do not
introduce a new `family` scope enum in the first Agentic Core slice.

Richer future sub-scopes such as `shared:family`, `shared:child:<id>`,
`shared:home`, or `shared:project:<id>` can be explored later when a concrete
family/child approval slice requires it.

## Team Inbox and Owner Inbox

### Team Inbox

Team Inbox is intake: files, PDFs, screenshots, voice notes, pasted text,
scanned letters, emails later, and capability events later. It should be
scope-aware.

### Owner Inbox

Owner Inbox is broader than the existing approvals surface. It covers:

- summaries
- memory proposals
- graph proposals
- action proposals
- escalation requests
- failed-job explanations
- routine outputs
- approval requests

Existing approvals should be reused where possible. Do not duplicate the
current approvals system accidentally: approvals are one mechanism that may
back some Owner Inbox decisions.

## Agent taxonomy

### Built-in Core agents

| Agent | First posture |
| --- | --- |
| Core Orchestrator | Built-in coordinator; deterministic routing first |
| Archivist Agent | Proposal-only document/capture processor |
| Memory Curator Agent | Proposal-only memory create/update/conflict flow |
| Researcher Agent | Read-only local corpus research briefs |
| Operator Agent | Proposes actions; execution approval-gated |
| Escalation Agent | Future controlled cloud/frontier context-pack path |
| System Health | System/admin diagnostics; no personal content by default |
| Daily Brief Agent | Future routine-backed agent; not first-wave required |

### Capability-provided or capability-enhanced agents

Graph Agent should be capability-provided or capability-enhanced. Core must not
require the graph capability to be present. The first slice can show
unavailable/disabled capability-provided agents only if the capability exists
and the UX supports it; otherwise defer.

Other later capability-provided candidates include Home, Calendar, Email,
School & Daycare, property, finance, and similar domain agents.

Capabilities may suggest agents; Core validates, constrains, instantiates,
runs, audits, and can disable them.

## Follow-up implementation outline after voice/capture

Only Phase 1 and Phase 2 are near-term after voice/capture. Everything else is
later.

### Phase 1 - Static agent registry and policy model (near-term)

- Code-defined built-in `AgentSpec` registry
- Code-derived/read-only `AgentInstance` view
- `EffectiveAgentPolicy` model/calculator
- Deterministic tests proving no wildcard permissions, no tool/action bypass,
  and no cross-scope leakage
- No persistent `AgentSpec`/`AgentInstance` tables

### Phase 2 - Web AI Team read-only dashboard (near-term)

- Read-only `/api/v1/agents` endpoint
- Lumogis Web AI Team page
- My / Family / System display
- Friendly permission summaries plus admin/debug details
- No runtime execution

### Phase 3 - Scope-aware capture to Team Inbox (later)

- Captured items can be personal/family/system/project
- Voice notes feed scoped capture queue
- Capture produces Team Inbox items

### Phase 4 - Owner Inbox / review queue (later)

- Scope-aware review items
- Summaries, memory proposals, graph proposals, action proposals
- Approval states that reuse existing approvals where possible

### Phase 5 - Read-only agent runtime (later)

- Core routes to read-only agents
- Agents request context through Core
- Archivist summarises; Memory Curator proposes; no mutation

### Phase 6 - Memory proposals (later)

- Memory Curator creates personal/family memory proposals
- User approves
- Writes are audited

### Phase 7 - Graph proposals (later)

- Graph Agent proposes entity/relation updates when graph capability exists
- User/admin approves
- Writes are audited

### Phase 8 - Capability-suggested agents (later)

- Capability manifests can suggest agents
- Core validates/constrains them
- Web shows built-in vs capability-provided agents

### Phase 9 - Operator Do workflow (later)

- Proposed actions
- Approval queue
- Controlled execution
- Audit trail

### Phase 10 - Escalation Agent (later)

- Minimal context packs
- Approval before cloud
- Model provider routing
- Audit

### Phase 11 - Family LAN polish (later)

- My AI Team / Family AI Team / System AI Team
- Household roles
- Family approvals
- Child-safe rules

## Recommended immediate next step

Do not implement Agentic Core until voice/capture is complete.

After voice/capture, start with:

1. code-defined static built-in `AgentSpec` registry
2. `EffectiveAgentPolicy` model/calculator
3. read-only `/api/v1/agents` endpoint
4. Lumogis Web AI Team page
5. deterministic tests proving no wildcard permissions, no tool/action
   bypass, and no cross-scope leakage

No LLM routing, runtime execution, capability-provided agents, writes, or cloud
escalation in the first slice.
