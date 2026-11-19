# ADR-269: Correct what kind an entry is: relabel in place, per-member name rules (LUM-967)
**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-16
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-967`); revised at /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-16 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-967-entity-kind-correction.plan.md`
**Exploration:** `.cursor/explorations/LUM-967-entity_kind_correction.md`
**Draft mirror:** `.cursor/adrs/LUM-967-entity_kind_correction.md`
**Linear:** LUM-967
**Related:** [ADR 266](266-lum-161-entity-browser.md) (the `entity_edit.py` write path, collision rule, graph via the stale rule), [ADR 259](259-lum-952-graph-reconcile-correctness.md) (orphan GC labels), [ADR 015](015-personal-shared-system-memory-scopes.md) (scopes), [ADR 158](158-graph-aware-entity-sharing-lum586.md) (shared entity projections)

## Context

Extraction often gives an entry the wrong kind ("Meier Pharmacy" as a person). A member needs to change the kind of their own entry from "What Lumogis knows" without losing its name, aliases, documents or links, and re-reading a document must not bring the wrong kind back.

Four facts shape the options:
- The FalkorDB node's **label** is the kind, and `create_node` MERGEs on label + id. A re-projection under a new label therefore creates a second node, and the orphan GC (which looks only for a missing Postgres row) never removes the old one.
- Extraction matches existing entries by name whatever their kind, but inserts a new entry with the model's kind when context tags don't overlap.
- Merge (LUM-965) doesn't exist yet.
- LUM-966's "names Lumogis won't learn" list needs the same per-member (name, kind) key.

## Decision

Add `change_entity_kind` to `services/entity_edit.py`, exposed as `entity_type` (alone) on `PATCH /api/v1/kg/entities/{id}`. It is limited to the owner's personal source row and the kinds PERSON, ORG, PROJECT and CONCEPT. It reuses ADR 266's lock, OCC `version`, embed-first search-point refresh and shared-projection refresh, and sets `graph_projected_at = NULL`. An empty stored kind counts as CONCEPT (how the graph labels it), so choosing the current kind is a no-op.

If another of the member's entries of the **target kind** already uses the entry's name or an alias, it refuses with `409 name_in_use` (plus `conflicting_entity_type`) until merge exists.

In the same transaction it writes a per-member rule into a new table `entity_name_rules(user_id, name_norm, from_type, action, to_type, …)` with action `retype`, keyed by the entry's canonical name and current kind. The table keeps one invariant: for a name, no rule's `to_type` is another rule's `from_type` (no chains, so one lookup gives the final kind), and after a correction to a kind no rule starts from that kind. Two same-name entries of different kinds can carry independent rules. The rule key is the canonical name at the time of the correction (NFC, whitespace collapsed, lower-cased). `store_entities` reads these rules once per batch on the extraction path only and rewrites the kind before storing.

In the graph, `project_entity` labels the node from the **stored row's** kind (the event's kind is only a fallback, so a late event cannot undo a correction). When the row is stale (`graph_projected_at IS NULL`), it first moves the node from each other entity-kind label to the target label in place, one label-scoped `MATCH (n:Other) … REMOVE n:Other SET n:Target` per other label (FalkorDB has no indexes here, so an unlabelled match would scan every node); this keeps the node id and its edges (spiked on FalkorDB v4.18.1). The row read, the relabel and the MERGE run under a per-entity lock in the writer process, so two projectors of one entry cannot interleave and leave a second node (which the relabel could not merge back). A failed row read raises before any graph write, and the stamp applies only if the stored kind **and `version`** are still the ones projected, so a correction that commits mid-projection (including a change and change-back) leaves the row stale for reconcile. The node then follows at the next reconcile, with no new event.

## Alternatives Considered

- **Kind-changed event + graph-service webhook:** correct, but adds a second sync path before any member surface reads kinds from the graph (ADR 266 revisit C2).
- **Delete and re-create the node:** loses co-occurrence counts, session edges and temporal-KG edge history.
- **Re-project only:** leaves a duplicate node that the GC can't see.
- **Dedicated corrections table:** duplicates LUM-966's key and lookup.
- **Flag on the entry row:** lost when the entry is merged or deleted.
- **Prompt hints to the model:** not deterministic.
- **Allow same-name, same-kind duplicates:** creates the mess merge has to clean up.
- **Wait for merge:** puts the stretch item behind the larger ticket.

Full detail: `.cursor/explorations/LUM-967-entity_kind_correction.md`

## Consequences

- **Easier:** correcting a kind keeps every link and edge, and the graph relabels a stale entry's node on its next projection. LUM-966 adds `action = 'block'` to the same table and applies retype, then block, in one lookup.
- **Persistence:** the correction is by name and kind, not by entry, so it survives rename, merge and delete. Two different things with the same name and the same model kind follow the member's last choice.
- **Same-name twin:** while merge doesn't exist, a second entry with the same name cannot be changed to a kind the first already has (`409`). The rule stops new wrong ones; the old twin waits for merge (LUM-965). A re-read that shares no context with the entry can still add a same-name entry, now of the corrected kind.
- **Retention:** rules hold names and outlive the entry. They are in the member's export, follow the same lifecycle as `entities` on user deletion, and are undone by changing the entry back; listing and removing them comes with LUM-966.
- **One writer process:** the per-entity lock assumes one writer process (Core runs one; the graph service runs one uvicorn process). A graph service and Core on different releases could still MERGE without the relabel; they ship together.
- **Stamp guard:** the entity stamp also requires the `version` that was read, so any concurrent edit of the entry (rename or kind change) during a projection leaves the row stale for reconcile, at the cost of one extra re-projection.
- **Explicit kinds win:** MCP `add_entity` with an explicit kind is not rewritten.
- **Graph lag:** the graph shows the old kind until the next reconcile (daily), the same as rename.
- **Mutable labels:** graph entity labels are now mutable in place. Anything that caches labels by node id must re-read them.
- **Aliases:** they get no rule (default). An alias that is really someone else keeps the model's kind.
- **Migration:** a new migration `082-entity-name-rules.sql`, mirrored in `postgres/init.sql`.

## Revisit conditions

- A member-facing page reads kinds or names from FalkorDB (LUM-196 map): add the `ENTITY_UPDATED` push (ADR 266 C2).
- LUM-965 ships merge: replace the kind-collision `409` with the merge offer.
- Members report that alias re-reads keep returning with the wrong kind: write rules for aliases too.
- Extraction gains a place type or the coding types (LUM-533/534): widen the allowed target kinds.
- FalkorDB adds label indexes on entity labels, or a FalkorDB upgrade changes `SET`/`REMOVE` label semantics: re-run the relabel live test.
- The graph service runs more than one worker process: the per-entity lock needs a cross-process guard.

## Status history
- 2026-11-16: Draft created by /explore
- 2026-11-16: Revised during /review-plan --arbitrate R1 — invariant restated (no chains); converge is label-scoped, gated on a stale row and run under a per-entity projection lock (a concurrent projection could otherwise leave a permanent duplicate node); stored kind labels the node; row-read failure fails closed; conditional stamp; consequences for same-name twins and rule retention recorded. Decisions C1 (`409`) and name-keyed rules unchanged.
- 2026-11-16: Revised by /verify-plan — implementation revealed two additive refinements, intent preserved: the entity stamp also checks the `version` read (closes the change-and-change-back case the kind check cannot see), and an empty stored kind counts as CONCEPT so "blank → CONCEPT" is a no-op rather than a rule the table's no-self CHECK would refuse. Finalised.
