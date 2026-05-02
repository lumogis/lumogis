# Lumogis Ecosystem Reference Model

> **Canonical references:** [`LUMOGIS_REFERENCE_MANUAL.md`](LUMOGIS_REFERENCE_MANUAL.md) (operators/contributors) and [`docs/decisions/`](decisions/) (ADRs). This document is a strategy narrative; prefer those for implementation truth.

## Purpose

This document defines the current reference model for Lumogis based on the latest strategy discussion. It is intended as the working blueprint for implementation planning in Cursor.

It covers:
- strategic direction
- architecture model
- open core vs premium boundaries
- plugin and capability model
- packaging and deployment model
- monetization and licensing model
- ecosystem and registry model
- client integration model, including Thunderbolt
- recommended next implementation steps

---

# 1. Strategic Direction

## Core decision

Lumogis should continue as an **open core, self-hosted platform**, but the commercialization boundary must be made much sharper.

The public repo is not the business itself. Its role is to:
- prove Ask/Do
- prove privacy-first architecture
- prove local ownership and self-hosting
- provide a trusted foundation for contributors
- establish ecosystem standards and extension points

The commercial opportunity should not sit in the generic shell or client layer.
It should sit in **premium capability services**, especially around:
- graph memory
- long-term context
- entity and relationship intelligence
- explainability and provenance
- advanced policies and governance
- premium capability packs

## Strategic response to Thunderbolt

Thunderbolt increases the pressure on the generic AI client layer.
Therefore Lumogis should not try to differentiate primarily through:
- generic chat UI
- model routing alone
- self-hosting alone
- MCP support alone
- local-first messaging alone

Instead, Lumogis should differentiate through:
- a modular, self-hosted capability ecosystem
- a strong Ask/Do trust model
- a premium memory and context substrate
- a client-agnostic toolbox model usable from multiple frontends

The correct strategic interpretation is:
- let the market commoditize generic AI clients
- let Lumogis own the deeper memory/context layer
- design that layer so it can work with Lumogis itself and external clients such as Thunderbolt

---

# 2. Target System Model

Lumogis should be treated as a **self-hosted local AI control plane plus capability ecosystem**.

It is not just:
- one app
- one UI
- one special premium add-on

It is:
- a foundation platform
- an ecosystem of plugins and services
- a runtime for local intelligence capabilities
- a trust layer for self-hosted AI
- a set of extension contracts that other clients can consume

This leads to a four-layer model.

---

# 3. Four-Layer Architecture

## Layer 1: Lumogis Core

### Role
The public, open, self-hosted foundation.

### Responsibilities
- runtime and orchestration
- Ask/Do model
- permissions and approval flow
- hooks, events, and extension points
- plugin loader
- basic connector framework
- local deployment baseline
- base UI or shell
- stable APIs and contracts
- base health and diagnostics

### Characteristics
- open source
- contributor-friendly
- trusted and inspectable
- useful on its own
- capable of loading optional extensions

### Design principle
Lumogis Core is the **control plane**.
It should remain clean, stable, modular, and not overloaded with premium-specific logic.

---

## Layer 2: Commodity Plugins

### Role
Useful ecosystem modules that expand the platform but are not the moat.

### Examples
- RSS reader
- web fetch/search helper
- Home Assistant connector
- voice transcription helper
- file extractors
- signal collectors
- action handlers
- storage adapters
- local utilities

### Characteristics
- can be open
- can be community-built
- can be Lumogis-maintained
- should be easy to plug in and replace

### Strategic role
These increase adoption and ecosystem value, but should not be treated as the primary monetization layer.

---

## Layer 3: Premium Capability Services

### Role
The commercial, differentiated intelligence modules.

### Examples
- graph memory
- entity extraction and resolution
- relationship modeling
- episodic memory
- semantic long-term memory
- timeline construction
- cross-source identity linking
- context ranking and packing
- provenance and explainability
- premium policy engine
- advanced writeback controls
- premium governance packs

### Characteristics
- self-hosted
- packaged separately from open core
- licensed
- accessible locally through stable interfaces
- usable by Lumogis and external clients

### Strategic role
This is where monetization lives.
These modules should not feel like "just nicer plugins".
They should feel like powerful local capability services.

---

## Layer 4: Client Access Layer

### Role
The set of ways internal or external clients consume Lumogis capabilities.

### Examples
- Lumogis native UI
- MCP interface
- local HTTP API
- gRPC if needed later
- CLI
- automations
- Thunderbolt adapter
- adapters for other local AI clients

### Strategic role
This makes Lumogis client-agnostic.
The premium capability services should not depend on one frontend.
They should be usable from multiple clients.

---

# 4. Plugin and Capability Taxonomy

Not all extensions need to be the same type.
Lumogis should support at least the following extension classes.

## A. Internal Plugins
These extend Lumogis only.
They do not need to be consumable by outside clients.

Examples:
- RSS ingestion
- extractors
- local automation glue
- internal signal collectors
- Home Assistant bridge used only within Lumogis

## B. Exportable Capability Services
These expose reusable tools to outside clients.
These are the most important category for commercialization.

Examples:
- memory service
- graph service
- context service
- explainability service
- premium write/policy service

## C. Client Adapters
These adapt Lumogis capabilities for specific client environments.

Examples:
- MCP server
- Thunderbolt-facing adapter
- CLI adapter
- local REST adapter

## D. Storage / Backend Adapters
These abstract infrastructure choices.

Examples:
- vector store adapter
- graph backend adapter
- metadata DB adapter

---

# 5. In-Process vs Out-of-Process

This is critical.

Not every plugin should be standalone.
Only the plugins or services that need:
- stronger isolation
- cross-client reuse
- commercial packaging
- independent scaling
- language/runtime freedom

should be designed as **out-of-process capability services**.

## In-process extensions
Good for:
- simple community plugins
- extractors
- small action handlers
- lightweight signal collectors

## Out-of-process capability services
Good for:
- premium graph and memory
- transcription service
- heavyweight connectors
- proprietary services
- services intended for external clients

### Rule of thumb
- simple commodity functionality can stay in-process
- monetizable or externally consumable intelligence should be out-of-process

---

# 6. Packaging and Distribution Model

Self-hosted does not mean open source.
Self-hosted only answers where the software runs.
Commercialization answers who owns the license to advanced capabilities.

## Recommended packaging model

### Open Core
- source-visible GitHub repo
- Docker Compose or local setup for easy self-hosting
- community installation path

### Commodity Plugins
- normal packages or source plugins
- optional Docker sidecars where useful

### Premium Capability Services
- separately packaged
- compiled or otherwise protected where appropriate
- distributed as local services
- can be shipped as:
  - OCI container
  - local binary/service
  - OS package
  - desktop-installed local daemon

### Important principle
Do not rely on Docker alone as a code protection model.
Docker is a distribution vehicle, not a protection boundary.
For proprietary modules, the implementation should be packaged in a form that is not just plain readable source.

## Curated distribution
Lumogis should likely offer an official curated distribution.

This means:
- the ecosystem stays open
- Lumogis publishes a recommended stack
- Lumogis chooses which plugins/services go into the official bundle
- premium services can be added to the official bundle through license unlocks

This is the best way to balance:
- openness
- modularity
- product polish
- commercial packaging

---

# 7. Commercialization Model

## Core principle
Do not commercialize the protocol itself.
Do not commercialize “having MCP”.
Do not commercialize generic plugin mechanics.

Commercialize **valuable capabilities**.

## What customers pay for
Users pay for:
- premium local intelligence
- advanced retrieval and reasoning quality
- better memory and context continuity
- explainability and provenance
- stronger policy and governance controls
- premium packaging, updates, and support

They do not need to pay for your cloud.
The software can still run fully on their own machine.

## Recommended license models

### Model A: Community + Pro
- community capability service with limited features
- pro capability service with advanced features

### Model B: One service, gated premium features
- service runs in community mode by default
- premium features unlock with local license

### Model C: Open protocol, commercial implementation
- API and MCP contracts are open
- the best implementation is licensed and commercial

### Best fit for Lumogis
A combination of B and C is likely strongest:
- open interfaces
- local self-hosted runtime
- premium services with local license validation

## License validation approach
Keep license validation local-first and privacy-compatible.

Recommended patterns:
- signed offline license file
- signed activation token
- optional periodic offline renewal

Avoid making premium services dependent on cloud connectivity for normal operation.

---

# 8. Free vs Paid Feature Boundary

Do not define the boundary simply as:
- read = free
- write = paid

That is too simplistic.

Instead define the boundary as:
- commodity capabilities = free/community
- differentiated intelligence = paid/pro

## Community examples
- basic storage
- basic retrieval
- basic local memory operations
- basic MCP exposure
- simple entity lookup
- simple timeline or history retrieval
- standard plugin execution

## Pro examples
- advanced entity resolution
- graph inference
- relationship linking across sources
- context ranking and packing
- richer timeline construction
- provenance and explainability
- advanced memory policies
- premium writeback controls
- premium governance and audit

Writes may be part of Pro, but the real value must be **better intelligence**, not just permission to write.

---

# 9. Capability Service Contract

To make plug-and-play real, every capability service should expose a standard contract.

## Required manifest fields
- name
- id
- version
- type: plugin / service / adapter
- mode: community / commercial
- transport: hook / http / grpc / mcp
- capabilities exposed
- permissions required
- configuration schema
- health check endpoint
- auth mode
- dependency requirements
- license mode

## Required runtime behaviors
- service discovery
- health reporting
- clean failure handling
- capability introspection
- version compatibility checks
- permission declaration
- license state declaration

This allows Lumogis Core to treat different services consistently.

---

# 10. MCP and External Consumption Model

The monetizable cross-client services should be consumable through a stable interface.

## Preferred interface options
1. MCP
2. local HTTP API
3. optional gRPC later if needed

## Why MCP matters
MCP gives Lumogis a path to be used by:
- Lumogis itself
- Thunderbolt
- other local AI clients
- automation environments

## Important design principle
The premium service is not “a plugin UI”.
It is a **self-hosted licensed capability service**.
MCP or local API is just the access path.

## Example premium service
`lumogis-memory-pro`

Possible community tools:
- memory.search_basic
- memory.store_basic
- memory.get_entity_basic

Possible pro tools:
- graph.resolve_entities
- graph.link_related_items
- memory.timeline_advanced
- context.build_pack
- context.explain_selection
- policy.enforce_sensitive_boundaries
- audit.show_write_history

This keeps the interface open while the premium implementation remains commercial.

---

# 11. Thunderbolt Integration Model

## Strategic interpretation
Thunderbolt is best treated as:
- a credible client ecosystem
- a validation of open, self-hosted AI clients
- a potential integration target
- not the layer Lumogis should primarily try to outcompete on

## Near-term assumption
Thunderbolt can be treated as a client that may consume Lumogis services via MCP or local API.

## Reference integration architecture

Self-hosted stack example:
- thunderbolt
- lumogis-core
- lumogis-memory-pro
- optional local graph/vector/metadata backends

### Interaction pattern
1. user runs Thunderbolt locally
2. user runs Lumogis premium memory/graph service locally
3. Thunderbolt connects to Lumogis through MCP or local API
4. Thunderbolt can call Lumogis tools such as:
   - graph.search_memory
   - graph.get_entity_summary
   - graph.timeline_for_project
   - context.build_pack
   - graph.explain_context

## Important commercial principle
Thunderbolt should not need to know Lumogis internals.
It only needs:
- the tool contract
- the permission model
- local authentication/authorization
- license-gated tool availability

## Plug-and-play meaning
Plug-and-play should mean:
- discoverable service
- stable tool contract
- local configuration only
- clear permissions
- community vs pro tool visibility
- no custom hacks per client

## Practical caution
Treat Thunderbolt integration as realistic but initially early-stage.
Lumogis should design for Thunderbolt compatibility without depending on Thunderbolt as the only route to market.

---

# 12. Ecosystem and Registry Model

Lumogis should explicitly evolve into an ecosystem.

## Ecosystem components
- Core platform
- plugin/service contract
- capability registry
- community plugins
- premium Lumogis capability services
- official curated distribution
- external client adapters

## Registry vision
The registry should eventually allow discovery of:
- open plugins
- community plugins
- official Lumogis plugins
- premium capability services
- integration adapters

Each registry entry should declare:
- category
- compatibility
- transport
- license mode
- maintainer
- maturity level

## Curated vs open ecosystem
Both should exist at once.

### Open ecosystem
- encourages experimentation
- community contributions
- broad integration surface

### Official curated distribution
- polished recommended setup
- version compatibility guaranteed
- premium services integrated cleanly
- better product experience for users

This is the right balance.

---

# 13. Risk Model

## Key risk
If the ecosystem is open, someone can build a free alternative to a premium capability.

## Correct interpretation
That risk is real and normal.
The answer is not to close the ecosystem.
The answer is to ensure the moat is not merely “only we can build it”.

## Real moat candidates
- best implementation quality
- best memory/context relevance
- best trust model
- best packaging
- best documentation
- best interoperability
- best support and updates
- strongest brand over time

---

# 14. Recommended Product Positioning

Lumogis should be positioned as:

**an open, self-hosted local AI control plane with a modular capability ecosystem**

And the commercial part should be positioned as:

**self-hosted premium intelligence modules for memory, context, graph reasoning, and governance**

This is better than positioning Lumogis as:
- just another AI app
- just another self-hosted chat UI
- just another MCP wrapper

A strong positioning line is:

> Use the client you like. Run Lumogis capabilities locally for private memory, context, and trustworthy action control.

---

# 15. Immediate Implementation Direction

## Phase 1: Formalize contracts
- define extension categories
- define manifest format
- define capability discovery model
- define local auth model
- define health check model
- define version compatibility rules

## Phase 2: Separate internal and exportable capabilities
- identify which existing modules remain internal
- identify which future modules should become exportable capability services

## Phase 3: Design the first premium service
Recommended first premium service:
- `lumogis-memory-pro`

Define:
- community vs pro tool set
- manifest
- MCP surface
- local API surface
- local license validation
- storage dependencies
- install and upgrade path

## Phase 4: Create curated distribution concept
- define official Lumogis bundle
- define optional service packs
- define plugin registry shape

## Phase 5: Add external-client proof point
- create one reference integration path
- ideally via MCP
- Thunderbolt-compatible in principle
- client-agnostic by design

---

# 16. Final Decision Summary

## Keep
- open core
- self-hosting
- modularity
- plugin and ecosystem direction
- commercialization opportunity

## Change
- make the monetization boundary much sharper
- stop thinking primarily in terms of one separate premium app
- think in terms of premium capability services
- design capabilities to be consumable by multiple clients

## Avoid
- trying to compete mainly on generic client features
- putting monetization too close to the shell
- overcomplicating the license split
- depending on one client ecosystem only

## Build toward
- open control plane
- capability ecosystem
- premium self-hosted memory/context services
- open contracts, commercial implementation
- curated official distribution
- compatibility with external clients like Thunderbolt

---

# 17. Working Principle for Cursor

When making architecture or implementation decisions, use this rule:

> If the feature is primarily about generic local AI app behavior, it belongs in Core or commodity plugins.
>
> If the feature is primarily about differentiated intelligence, memory, context continuity, explainability, governance, or premium trust behavior, it belongs in a premium capability service.
>
> If the feature needs to be usable by external clients, it must expose a stable local API or MCP surface.

---

# 18. One-Sentence Reference Model

Lumogis is an open, self-hosted local AI control plane with a modular plugin and capability ecosystem, where commodity extensions remain open and community-friendly, while premium self-hosted capability services deliver monetizable memory, graph, context, explainability, and governance features through stable interfaces consumable by Lumogis itself and external clients such as Thunderbolt.
