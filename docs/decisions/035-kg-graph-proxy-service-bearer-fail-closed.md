# ADR 035: Core `query_graph` HTTP proxy — service bearer fail-closed (LUM-42 / FP-049)

**Status:** Accepted  
**Created:** 2026-05-08  
**Last updated:** 2026-05-08  
**Decided by:** `/verify-plan` after implementation of `LUM-42-fp049-kg-capability-auth-hardening.plan.md`.

## Context

When **`GRAPH_MODE=service`**, Core registers a ToolSpec that forwards LLM **`query_graph`** calls to the **`lumogis-graph`** **`POST /tools/query_graph`** endpoint via `graph_query_tool_proxy_call`. Historically that path passed **`require_service_bearer=False`** into the generic capability HTTP helper, so Core could open a TCP connection **without** `GRAPH_WEBHOOK_SECRET` even though generic out-of-process capability dispatch **fail-closes** when a bearer is required but missing.

The KG service still enforces **`check_webhook_auth`** (shared secret / insecure-dev matrix). That does **not** justify Core silently attempting unsigned bridge calls: operator intent on Core should be explicit, and behaviour should align with the generic OOP posture unless a deliberate legacy opt-in is set.

**Out of scope (unchanged by this ADR):** `graph_webhook_dispatcher` and other **`get_kg_webhook_secret()`** consumers; **`GRAPH_MODE=inprocess`** (in-process plugin path); manifest invoke URL formalisation (**LUM-41**); compose policy guard for new services (**LUM-43**).

## Decision

1. Core exposes **`config.get_graph_proxy_require_service_bearer() -> bool`** implementing the normative matrix:
   - Secret present → **`True`** (bearer sent when configured).
   - Secret absent + **`LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET`** in **`1` / `true` / `yes`** (strip + lower) → **`False`** (legacy: POST may omit `Authorization`).
   - Otherwise → **`True`** (fail-closed: **no outbound HTTP**; `missing_service_auth` / user-facing **`GRAPH_QUERY_UNAVAILABLE`** string).

2. **`graph_query_tool_proxy_call`** sets **`require_service_bearer=`** from that helper. Core **does not** read **`KG_ALLOW_INSECURE_WEBHOOKS`** from the KG process to decide this flag.

3. Operators are directed to set **`GRAPH_WEBHOOK_SECRET`** symmetrically on Core and KG, **or** set the Core opt-in for LAN/dev only — documented in **`services/lumogis-graph/README.md`**, **`docs/LUMOGIS_REFERENCE_MANUAL.md`** §11, and **CHANGELOG** BREAKING notes.

## Consequences

- **Breaking:** Existing deployments relying on KG-only insecure webhooks without a Core secret lose **`query_graph`** until they configure the secret or Core opt-in.
- **Positive:** Predictable parity with generic capability fail-closed behaviour; no accidental egress with missing Core configuration.
- **Tests:** Matrix in **`test_config_graph_proxy_require_service_bearer_matrix`**; proxy tests migrated with legacy opt-in or secret; explicit fail-closed and legacy opt-in assertions in **`test_query_graph_proxy.py`**.

## Status history

- **2026-05-08:** Finalised by `/verify-plan` — implementation confirmed decision.
