# ADR-051: CONTEXT_BUILDING entity selection — hybrid deterministic + opt-in semantic pass

**Status:** Accepted
**Created:** 2026-05-17
**Last updated:** 2026-05-17
**Decided by:** `/verify-plan` after implementation of `LUM-210-context-building-hybrid-entity-selection.plan.md` (implements exploration Option C for **LUM-210**).

## Context

The `CONTEXT_BUILDING` phase on the chat hot path had no documented entity-selection policy, budget, or ranking when multiple entities match. `on_context_building` used a fixed cap and omitted `user_id` on the hook, which broke per-user visibility for Postgres and Qdrant. Downstream work (**LUM-98** ContextBuilder, **LUM-156** PreCompact, **LUM-202** domain scores) needs a stable selector contract.

## Decision

1. **Hybrid selection (Option C):** deterministic word-boundary match over the user’s high-signal entity pool, optional **Qdrant** semantic top-up on the `entities` collection when **`LUMOGIS_CONTEXT_BUILDER_SEMANTIC=true`** and an embedder is available (default **false**). Candidates merge, dedupe by `entity_id`, gate on **`MIN_MENTION_COUNT`**, rank with
   `α·sem_score + β·log(1+mention_count) + γ·domain_boost + δ·recency_from_updated_at + explicit_bonus`
   with **γ=0** (stub) and **δ=0** by default, then truncate to **`LUMOGIS_CONTEXT_ENTITY_BUDGET`** (default **5**).

2. **Stable API:** `select_context_entities(query, user_id) -> list[dict]` in `services/lumogis-graph/graph/query.py` (re-exported from `orchestrator/plugins/graph/query.py` for tests) returns ordered Postgres-shaped rows **before** `ego_network` expansion. `on_context_building` calls it, then renders `[Graph]` lines.

3. **`Event.CONTEXT_BUILDING` payload:** callers pass **`user_id`** (default **`"default"`**). Core `routes/chat.py` passes the authenticated user; KG **`POST /context`** passes **`body.user_id`**; MCP passes through from context when present.

4. **Token allocator:** a dedicated **`entities`** fractional slot (default **0.05** of context) holds graph corpus from this hook; **`plugin_context`** is reduced accordingly. **`pooled_budget`** for non-sanitiser plaintext truncation includes **`entities`** so graph tokens are not silently dropped.

5. **Service-mode parity:** **`max_fragments`** for KG fetch uses **`min(get_context_entity_budget(), 20)`** when not overridden, aligned across Core dispatcher and **`ContextRequest`**.

6. **Config mirrors:** every new Core getter for entity budget / semantic flags / rank coefficients has an identically named env-backed mirror in **`services/lumogis-graph/config.py`** so **`GRAPH_MODE=service`** does not raise **`AttributeError`**.

## Consequences

- **Easier:** LUM-98 / LUM-156 can import **`select_context_entities`**; LUM-202 wires **`domain_boost`** at **`γ`**; operators tune behaviour via documented **`LUMOGIS_CONTEXT_*`** env vars (`config/test.env.example`).
- **Harder:** Vendored **`services/lumogis-graph/models/webhook.py`** must stay in lockstep with **`orchestrator/models/webhook.py`** (**`make sync-vendored`**).
- **v1 ranking posture:** With default coefficients, semantic candidates mainly fill budget slots not already taken by explicit matches; aggressive semantic-over-explicit tuning waits for an offline replay harness (**deferred**; see plan Follow-up register).

## Status history

- **2026-05-17:** Finalised by `/verify-plan` — implementation confirmed decision (draft: `.cursor/adrs/LUM-210-context-building-entity-selection.md`).
