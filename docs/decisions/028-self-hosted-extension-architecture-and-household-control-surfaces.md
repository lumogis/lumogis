# ADR 028: Self-hosted extension architecture and household control surfaces

## Status

**Accepted** (2026-04-26). Records architecture outcomes of the **self-hosted remediation** programme through **Phase 4** (Phases 0–4: vocabulary, boundary hygiene, unified tool catalog, capability HTTP execution bridge, flag-default-off LLM/OOP wiring, Lumogis Web curated facades, admin diagnostics, dev workflow docs). This ADR is **decision-focused**, not an implementation log.

## Context

Lumogis was originally organised around five **contributor** concepts — **services**, **adapters**, **plugins**, **signals**, and **actions** — each with a clear place in the tree and import boundaries.

Subsequent work added **cross-cutting platform** concerns: **household multi-user** identity and roles, **per-user / household / system** connector credentials, **capability** discovery and health, **MCP** as a first-party transport, **Knowledge Graph** as an optional out-of-process service, a **unified read model** for tools, and **guarded** execution of capability-backed tools from the LLM loop. The **architecture audit** concluded that modular scaffolding was sound but **contracts** around tools vs capabilities vs clients and **Web/API** boundaries needed tightening.

The **product target** remains **self-hosted family / household AI**. **Hosted SaaS multi-tenancy**, **marketplace-grade** third-party plugins, and **untrusted** arbitrary code in Core are **explicitly out of scope** for this framing.

## Decision

### A. Core remains the policy and execution kernel

**Lumogis Core (orchestrator)** owns: **identity** and session context, **auth** semantics, **permissions / Ask–Do**, **credential resolution** (including tiers), **audit and correlation hooks**, **tool execution policy** (when and how tools run), **capability discovery/registry**, and **stable HTTP facades** for first-party clients.

**Clients** (e.g. Lumogis Web, MCP-speaking agents) and **capability services** (e.g. `lumogis-graph`) **consume** Core contracts; they do **not** own household policy or credential encryption.

### B. Preserve the five-pillar contributor model

The **five** contributor types remain: **services**, **adapters**, **plugins**, **signals**, **actions**.

The **unified tool catalog** and **capability execution overlay** (`ToolCatalog`, `ToolExecutor`, HTTP proxies, request-scoped OOP routing) are **cross-cutting infrastructure** on top of those pillars — **not** a sixth pillar.

### C. Tool catalog and execution overlay

- **`ToolCatalog`** is the **read model** aggregating tools from **core**, **plugin**, **proxy**, **MCP**, **action**, and **registered capability** sources (see tool vocabulary).
- **`ToolExecutor`**, **`CapabilityHttpToolProxy`**, and **request-scoped** OOP tool routes provide a **guarded** execution path for capability-backed tools when the feature flag is on.
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults to **`false`**; when off, the LLM loop uses the legacy **`TOOLS`** list and does not register OOP routes for that request.
- **In-process `ToolSpec`** dispatch **wins** over OOP for **name collisions** (`run_tool` resolves `TOOL_SPECS` first).
- **MCP** remains a **separate transport** with its own stable surface; **intentional divergence** from the LLM tool list is **documented and regression-tested** (manifest parity tests).
- **OOP** capability tool execution must **fail closed** or return **safe** error payloads — never leak secrets or raw stack traces to clients.
- **Full** persistence of OOP executions into the **`audit_log`** table remains **deferred**; structured logging / **`ToolAuditEnvelope`** hold correlation in the interim.

### D. Lumogis Web consumes curated API facades

- **Lumogis Web** must **not** depend on credential **payload** shapes, raw storage schemas, Python handler paths, or other **internals**.
- The first-party client consumes **curated** **`/api/v1/me/*`** and **`/api/v1/admin/*`** JSON facades.
- **Shipped examples (Phase 4):**
  - `GET /api/v1/me/tools`
  - `GET /api/v1/me/llm-providers`
  - `GET /api/v1/me/notifications`
  - `GET /api/v1/admin/diagnostics`
- These endpoints are **read-only** **household-control / operator** views unless a future ADR explicitly adds safe writes.
- **Legacy** HTML admin / dashboard routes **remain** for migration; they are **not** the preferred integration surface for new Web features.

### E. Optional capability services follow household contracts

- **`lumogis-graph`** remains the **reference** optional capability service (manifest, `/health`, HTTP tool contract).
- Capability services interact with Core via **HTTP contracts**, not **shared Core database** credentials by default.
- **`X-Lumogis-User`** is **attribution only** and is meaningful only on **authenticated** capability-service requests (never standalone proof of identity).
- **Shared secret / bearer** between Core and a capability is acceptable for **household LAN** trust.
- **mTLS by default**, **signed marketplace manifests**, **arbitrary third-party connector sandbox**, and **hosted multi-tenant** isolation remain **deferred** (later phases / separate ADRs).

## Consequences

**Positive**

- Clearer **vocabulary** (tool vs capability vs action vs MCP) for contributors.
- **Safer** Web/API boundaries via **DTOs** and thin routes.
- **One observable catalog** for “what tools exist” and availability hints.
- **Feature-flagged** capability execution preserves **flag-off parity** with pre-unified behaviour.
- New capability services can align with **HTTP** patterns without **graph-only** special cases in Core.
- Household **operator** surfaces can grow through **stable** v1 facades.

**Trade-offs**

- More **layers** and **DTOs** to maintain.
- Some catalog fields remain **approximate** (e.g. **`permission_mode`** not fully DB-resolved per user).
- **OOP** audit is **not** yet uniformly persisted in **`audit_log`**.
- **MCP** and **LLM** tool surfaces **intentionally differ**; operators must read both contracts where relevant.
- **Phase 5** still owes a narrow **reference + mock capability** proof for generalised contracts (per remediation plan).

## Non-goals / deferred

- Cloud **multi-tenant SaaS** posture.
- **Public** plugin/connector **marketplace** and **PyPI-style** Plugin SDK.
- **Untrusted** third-party plugins or **dynamic signed** connector manifests in Core.
- **mTLS** as default for household deployments.
- Full **sync→async** migration of the orchestrator.
- **Removal** of the in-process graph plugin before Web parity (tracked separately).
- **Removal** of legacy admin/dashboard routes until Web covers workflows.

## References

**Architecture guidance (remediation)**

- [`docs/architecture/lumogis-self-hosted-platform-remediation-plan.md`](../architecture/lumogis-self-hosted-platform-remediation-plan.md)
- [`docs/architecture/self-hosted-remediation-consolidation-review.md`](../architecture/self-hosted-remediation-consolidation-review.md)
- [`docs/architecture/phase-4-household-control-surface-closeout-review.md`](../architecture/phase-4-household-control-surface-closeout-review.md)
- [`docs/architecture/tool-vocabulary.md`](../architecture/tool-vocabulary.md)
- [`docs/architecture/plugin-imports.md`](../architecture/plugin-imports.md)

**Related ADRs**

- [ADR 005 — Plugin boundary](005-plugin-boundary.md)
- [ADR 010 — Ecosystem plumbing](010-ecosystem-plumbing.md)
- [ADR 011 — Lumogis graph service extraction](011-lumogis-graph-service-extraction.md)
- [ADR 012 — Family LAN multi-user](012-family-lan-multi-user.md)
- [ADR 017 — MCP token user map](017-mcp-token-user-map.md)
- [ADR 018 — Per-user connector credentials](018-per-user-connector-credentials.md)
- [ADR 027 — Credential scopes / shared-system](027-credential_scopes_shared_system.md)
