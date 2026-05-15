# ADR-002: Graph capabilities as an optional extension (Protocol + optional backend)

## Context

Graph storage — nodes for people, organisations, documents, and relationships
between them — is a legitimate building block for extensions. Contributors
should implement graph features against a stable **GraphStore** Protocol
without imposing graph infrastructure on operators who do not need it.

Bundling a concrete graph database into the default Compose stack would burden
every household installation and implicitly favour one backend implementation.

## Decision

Graph capability is exposed through the **GraphStore** Protocol
(`ports/graph_store.py`). Core does not require a graph backend in the minimal
configuration: graph plugins and integrations opt in via configuration and
dependency wiring.

Implementations of **GraphStore** (Falkor-backed or otherwise) are treated as
**optional capability**: a reference FalkorDB-backed implementation and the
full-stack graph wiring used by the maintainers ship as a **premium capability
available separately** from the minimal public AGPL snapshot. The Protocol and
schema expectations remain the public contract (`docs/extending/extending-the-stack.md`).

This follows the same layering used elsewhere: **ports/** define contracts;
**adapters/** provide concrete backends when present; Compose overlays activate
backing services only when operators merge them deliberately.

### Why FalkorDB as a reference backing technology

Where the premium Falkor reference is used, FalkorDB offers a pragmatic default:
MIT-licensed Redis-protocol deployment, widely known **Cypher** queries, and a
compact single-container footprint. **Other Cypher-compatible or bespoke
implementations remain valid**: the Protocol boundary is intentional so backends
stay swappable without forking Core.

## Consequences

- **Minimal installs stay unaware of graph infra** unless overlays and env are
  added.
- **`GraphStore` remains the abstraction** for contributed graph tooling.
- **Premium / full-tree operators** adopt the Falkor reference (or equivalents)
  from the separately distributed capability bundles; contributors without that
  tree still rely on the public Protocol docs.
- **No Redis-backed graph substrate in Core by default.** Optional stacks bring
  their backing services via explicit Compose merges.
