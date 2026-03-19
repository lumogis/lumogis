# ADR-005: Plugin boundary at the plugins/ directory

## Context

lumogis needs a plugin boundary that is structurally enforced, not policy-enforced. Plugins are optional extensions: core must work completely without them, and dropping a plugin in must re-enable its functionality automatically with no changes to core code.

## Decision

Plugins live **only** under named directories in `plugins/` (e.g. `plugins/my-plugin/`). The orchestrator's plugin loader discovers and mounts whatever packages exist at startup. Absence of any plugin is a valid, fully-supported configuration — hooks fire, nothing listens, no errors.

Named plugin directories are listed in `.gitignore` so that out-of-tree plugin packages can be mounted at deploy time without being tracked in the core repository.

## Consequences

- **Structural enforcement:** The boundary is in the filesystem layout, not documentation or convention.
- **Zero coupling:** Core services never import plugin code. All extension points go through `hooks.py`.
- **Composable deployments:** Any combination of plugins can be mounted independently.
- **CI:** Core tests run without any plugins present.
