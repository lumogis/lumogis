# ADR-206: The commercial line for email intelligence is the graph boundary

**Status:** Accepted
**Created:** 2026-08-06
**Last updated:** 2026-08-06
**Decided by:** `/explore LUM-809`
**Linear:** [LUM-809](https://linear.app/lumogis/issue/LUM-809/explore-where-the-commercial-line-falls-for-email-intelligence-which)
**Extends:** [ADR-205](205-lum-655-email-core-vs-plugin.md) (email is a core, opt-in integration) — this settles the question ADR-205 deferred
**Relates:** [ADR-101](101-lum-442-commercialisation-ecosystem-model.md) §3 pricing principle and §9 one-rail entitlement, [ADR-042](042-kg-public-private-export-boundary.md) (KG public/private boundary), LUM-516 / LUM-517 (the precedent), LUM-262 (enforcement), LUM-169 (model-cost line), LUM-654 / LUM-703 (re-scoped by this)
**Exploration:** `.cursor/explorations/LUM-809-email-intelligence-commercial-line.md`
**Draft mirror:** `.cursor/adrs/email-intelligence-commercial-line.md`

## Context

ADR-205 settled that base email is core and AGPL, and named the monetisation layer as managed Remote (LUM-506) plus "advanced email intelligence" as app-scope plugins. It did not say which behaviours those are. That gap blocked LUM-654 and put LUM-684 (on-box compose refine) at risk — refine already ships free in core, so any line drawn later either includes it or takes it away from users who already have it.

Three facts, none of them in the ticket, decide the answer.

**Nothing email-related is proprietary today.** `scripts/public-export-strip-list.txt` strips exactly the bundled build artefacts, the knowledge graph (`services/lumogis-graph/`, `orchestrator/plugins/graph/`, `adapters/falkordb_store.py`, `services/kg_premium_core.py`, `services/edge_quality.py`, the premium compose files), and maintainer docs. No email, IMAP, SMTP, or refine path appears, and `docs/public-export/AGENTS.md` names the whole email arc (ADRs 174–202) as shipping in the public AGPL tree.

**Email intelligence has no graph dependency.** Across `orchestrator/services/email_*.py`, `services/text_refine.py`, and `actions/handlers/smtp_send.py` there is no reference to `plugins.graph`, `GRAPH_MODE`, `GraphStore`, or `kg_premium`. It runs on `services/local_llm_task.run_bounded_completion` — on-box Ollama. `services/relevance_learning.py` and `services/feedback.py` (ADR-181) are plain core Postgres work. `GRAPH_MODE` defaults to `"disabled"` (`config.py`).

**The split has already been done once.** LUM-516 ships basic household-biography synthesis in core; LUM-517 adds graph-enriched synthesis as premium, gated on effective `GRAPH_MODE != "disabled"` with **no new env var**, degrading silently to the core path when the graph is absent — "mirror `null_*` adapter pattern. Never error."

## Decision

~~**An email behaviour is premium if and only if it reads the knowledge graph. Otherwise it is core.**~~

> **Re-decided 2026-11-07 by [ADR 229](229-lum-855-kg-agpl-relicence.md).** The knowledge graph is
> now AGPL and ships publicly, so this rule has no referent. **All email intelligence is core and
> AGPL.** The mechanism is unchanged: the `GRAPH_MODE != "disabled"` check stays at every call
> site, reinterpreted as a **capability** check (is the graph deployed?) rather than a commercial
> one, with the same silent-degradation contract — graph absent, fall back to the core path, never
> error, never a blank surface. **No code change in the email stack.** The table below still reads
> correctly; read "premium" as "needs the graph deployed".

No new email-specific gate, no new environment variable, no new entitlement SKU.

- **The gate** is the existing effective `GRAPH_MODE != "disabled"` check at the call site (`config.read_graph_mode_from_env` → `main.py::_wire_graph_mode_handlers`, falling back to `disabled` when premium wiring is absent via `services/kg_premium_core`).
- **The contract** is graceful degradation: graph absent → fall back to the core path **silently**. Never an error, never a blank surface.
- **Enforcement** remains LUM-262, unduplicated.

Applied to the behaviours in question:

| Behaviour | Scope | Why |
|---|---|---|
| Compose refine (LUM-684) | **core** | On-box LLM; already shipped free |
| Learned relevance (ADR-181) | **core** | Postgres feedback loop; no graph |
| Agentic intent routing (LUM-654) | **core** | On-box NLU; contact resolution available without the graph (see below) |
| Cross-source reasoning | **premium** | Derives its value from graph traversal |
| Commitment extraction, dormant-contact nudges (LUM-703) | **premium** | Relationship-graph shaped |

**On contact resolution specifically.** LUM-654 needs to resolve "Mum" to an address, which looks graph-shaped but is not. The entity store (`orchestrator/services/entities.py`, `models/entities.py`) carries no graph dependency — only `services/document_entity_cascade.py` does — and `smtp_send.py::_is_first_contact` already resolves recipients against prior contact history in core. So base resolution is core. A *graph-enriched* resolution (relationship clusters, typed edges, disambiguating "Mum" through inferred household relationships) is the premium enrichment on top, and owes the same silent fallback to the core resolver.

## Alternatives Considered

- **Agentic vs assistive** — paywalls "acting" although email send already ships free in core (LUM-650, ADR-185), and charges for zero-marginal-cost local compute, which ADR-101 §3 forbids. Local-LLM email triage is also a 2026 commodity, so the paywall would not hold in practice. Rejected.
- **Model cost (cloud-LLM features premium)** — real and honest under ADR-101 §3, because Lumogis would carry the recurring inference cost. But it is product-wide and already owned by **LUM-169** (managed-key subscription). Email inherits whatever LUM-169 settles; it is not an email decision. Deferred, not adopted.
- **A dedicated "Email Pro" entitlement SKU** — breaks ADR-101 §9's one-generic-rail rule, fragments a one-sentence pricing story, and is the textbook crippled-core move. Rejected.

Full option table and external precedents are in the exploration.

## Consequences

**Easier**
- LUM-654 is unblocked and lands in core, where its on-box implementation already wants to be.
- Zero new commercial infrastructure: no SKU, no env var, no second gate to keep consistent with the first.
- ADR-205's promise that base email stays free is kept without further argument, and LUM-684 is not retroactively taken away.
- The pricing story stays one sentence: Remote is rented because Lumogis runs it; the graph is bought once because it runs on your hardware.

**Harder / foreclosed**
- Email can never become a subscription line without overturning this ADR and ADR-101 §9.
- Every future premium email behaviour owes a graceful-degradation path — two code paths and tests for both, exactly as LUM-517 scoped.
- LUM-262's blast radius grows: it becomes the enforcement point for premium email intelligence as well as the KG. Until it is built the gate is a capability check, not a licence check — the same exposure the KG already carries, but now with a second dependent.

**Future chunks must know**
- Do not add an email feature flag for commercial purposes. The graph check is the only qualifying test.
- A behaviour that is neither on-box nor graph-derived (e.g. cloud-model-dependent) belongs to LUM-169, not here.

## Revisit conditions

- If the knowledge graph ever ships free or default-on, this ADR loses its boundary and must be re-decided — the graph check would then gate nothing.
- If LUM-169 lands a managed-key subscription, revisit whether any email behaviour depends on cloud inference and therefore falls under that line instead.
- If a genuinely valuable email behaviour appears that is neither on-box nor graph-derived, the rule has a gap and needs a third case rather than a stretched reading.
- If ADR-101's pricing principle is ever amended to permit renting pure-local capability, this decision rests on a premise that no longer holds.

## Status history

- 2026-08-06: Draft created by `/explore LUM-809`.
- 2026-08-06: Finalised and numbered. LUM-654 re-scoped from "app-scope, deferred" to core as a direct consequence.
- 2026-11-10: public-boundary wording (LUM-898).
