# ADR-005: Open-core boundary at the plugin directory

## Context

lumogis-core is AGPL; lumogis-app ships proprietary intelligence (graph, ambient, voice, context, mesh). The boundary must be enforceable in repo layout, not only policy.

## Decision

Proprietary plugins live **only** under named directories (`plugins/graph/`, `plugins/ambient/`, etc.) in **lumogis-app**. **lumogis-core** `.gitignore` excludes those paths so they cannot be committed to the public tree. The open orchestrator loads whatever plugin packages exist; absence of proprietary plugins is a valid, supported configuration.

## Consequences

- **Structural enforcement:** Not an honour system — the public repo literally cannot contain those trees.
- **Clear upgrade path:** Users add lumogis-app plugins by mounting or merging repos at deploy time.
- **CI:** Core tests and releases never depend on proprietary code.
