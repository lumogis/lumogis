# ADR-042: KG ships in `lumogis-app` only — public AGPL export boundary

**Status:** Finalised
**Created:** 2026-05-15
**Finalised:** 2026-05-15 (`/verify-plan` LUM-242 — implementation confirmed)
**Decided by:** Exploration LUM-242 + plan `LUM-242-kg-public-private-boundary`; arbitration R1 2026-05-15

## Context

The knowledge-graph (KG) implementation (in-process plugin, Falkor adapter, Core webhook dispatcher, proxy ToolSpec wiring, `services/lumogis-graph/`, premium compose overlays) must not appear in exported public AGPL snapshots at `lumogis/lumogis`. Operators still configure `GRAPH_MODE` (`inprocess`, `service`, `disabled`); Core must fail closed / degrade gracefully when premium modules are absent. The **GraphStore** Protocol, webhook wire models, and `Event.*` graph-related constants remain the public contract for third-party or premium implementations.

Historical framing (e.g. ADR-011, some LUM-238 audit text) assumed more KG surface in the public tree; this ADR locks the **distribution boundary** for the public export line.

## Decision

1. **Export-time strip:** A single strip-list source (`scripts/public-export-strip-list.txt`) drives `scripts/create-upstream-export-tree.sh` and `scripts/check-public-export.sh` so the public tree omits KG implementation paths, selected historical KG ADRs (007, 008, 009, 011, 035), and `orchestrator/tests/premium/`.
2. **Runtime contract:** Default **`GRAPH_MODE` is `disabled`**. `service` / `inprocess` remain valid env tokens; if premium wiring or the in-process plugin is missing, Core logs **one structured WARNING per scenario** and publishes **effective mode `disabled`** via `config.set_effective_graph_mode_for_process` so `get_graph_mode()`, chat, and API guards agree after lifespan wiring.
3. **Premium module split:** Query-graph proxy registration and related helpers live in strippable `orchestrator/services/kg_premium_core.py` (omitted from export), not in public `tools.py` / `capability_http.py`.
4. **ADR-002 (public):** Remains the Protocol + optional-backend narrative; **Falkor-backed reference** is described as **premium / separately distributed**, not bundled in the minimal public snapshot.

## Alternatives considered

- **True multi-repo split of KG** — deferred (overhead, contract vendoring).
- **Licence flag only (no strip)** — rejected (weak enforcement vs distribution clarity).
- **Strip only plugin; keep service AGPL** — rejected (half-measure vs product story).

## Consequences

- Commerce and AGPL narrative align: inspectable core vs premium KG bundle in private workspace / separate distribution.
- **`make verify-public-rc`** and export scripts are the enforcement hinge; contributors must keep strip list, guards, and docs in sync.
- Downstream issues (**LUM-238**, **LUM-40**, **LUM-96**, **LUM-241**) must update acceptance text that still assumes “KG fully AGPL” where that conflicts with this boundary.
- **ADR-011** and other stripped ADRs stay in **private** history; **public** export omits them **by path**, not by rewriting history.

## Status history

- 2026-05-15: Draft in `.cursor/adrs/lum_242_kg_public_private_boundary.md` (exploration + arbitration).
- 2026-05-15: Finalised in product `docs/decisions/` — implementation verified (`/verify-plan` LUM-242).
