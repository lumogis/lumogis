# Plugin import boundaries (Lumogis Core)

This document refines the plugin rules in the root `ARCHITECTURE.md` and [ADR-005: Plugin boundary](../decisions/005-plugin-boundary.md). **Out-of-tree** plugins and **new** in-tree plugins should treat the default rule as the contract; the `config` exception below applies only to **documented, first-party** code.

## Default rule (preferred for all new plugin code)

Plugins may import from:

- `ports/`
- `models/`
- `events.py`
- `hooks.py`

Plugins must **not** import from:

- `services/`
- `adapters/`

`config.py` is the place Core constructs concrete adapters; plugins are not a second `config` surface.

## Documented exception: `config` in first-party in-tree plugins

The shipped graph plugin (`orchestrator/plugins/graph/__init__.py`) imports `config` to read **graph mode** and obtain graph-related factories in line with the rest of the orchestrator. That is an intentional **in-tree, first-party** carve-out, not a general pattern for untrusted or third-party plugin packages.

- **Allowed when:** the plugin is part of this repository, peer-reviewed, and the import is limited to `config` factories needed for the same reason Core uses them (e.g. `get_graph_mode()`, `get_graph_store()`).
- **Not allowed as an exception:** reaching into `services/*`, `adapters/*`, or broad use of `config` to bypass ports.

New capabilities that need a mode switch or HTTP discovery should follow **out-of-process capability services** and `CapabilityRegistry` (see `ARCHITECTURE.md` §Ecosystem plumbing) rather than growing new `config` exceptions in Core plugins.

## Related

- [ADR-005: Plugin boundary](../decisions/005-plugin-boundary.md) — “Core services never import plugin code” (unchanged).
- [Self-hosted remediation plan](lumogis-self-hosted-platform-remediation-plan.md) — Phase 0 / Chunk 1 vocabulary.
