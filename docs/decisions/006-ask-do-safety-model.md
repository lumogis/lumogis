# ADR-006: Ask/Do instead of capability-based or role-based access control

## Context

The LLM can invoke tools and actions that read or write user data. Classic RBAC or capability lists are hard to explain to end users and brittle as tool sets grow.

## Decision

Per-connector **Ask** vs **Do** mode: **Ask** blocks writes until the user elevates the connector (or approves per action). **Do** allows immediate execution for that connector’s writes. **Routine elevation** promotes well-behaved actions after repeated clean approvals. Hard-limited action types never auto-elevate.

## Consequences

- **Trust model:** Matches how users think — “can this thing change my stuff without asking?”
- **Auditability:** Every permission check logs to `action_log`; action executions log to `audit_log`.
- **Elevation path:** Power users can move from cautious to automated without re-architecting security.
- **Trade-off:** Not fine-grained per-field ACLs; intentional — self-hosted single-user first.
