# ADR-272: Delete an entry so Lumogis does not learn it again (LUM-966)
**Status:** Finalised
**Created:** 2026-11-17
**Last updated:** 2026-11-17
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-966`); revised at /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-17 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-966-entity-delete-wont-learn.plan.md`
**Linear:** LUM-966
**Exploration:** `.cursor/explorations/LUM-966-entity_delete_wont_learn.md`
**Draft mirror:** `.cursor/adrs/LUM-966-entity_delete_wont_learn.md`
**Related:** [ADR 266](266-lum-161-entity-browser.md) (member write path, lock, OCC), [ADR 269](269-lum-967-entity-kind-correction.md) (`entity_name_rules`), [ADR 270](270-lum-965-entity-merge-undo.md) (`entity_merges`), ADR 259 (orphan GC), ADR 158 (shared entity projections), ADR 015 (scopes)

## Context

A member can see, rename, retype and merge her entries in "What Lumogis knows", but she cannot remove a wrong one. If she could, the next re-read of the same document would learn it again. Thomas decided the behaviour on 2026-11-12 (theme "See what Lumogis knows", Decision 1):
- delete everywhere, for the owner only;
- a per-member list of names Lumogis won't learn, scoped to the kind;
- Hide for other members' shared entries;
- no trash period, with the names list as the undo.

Several facts shape the mechanism:
- Deleting the source row cascades to mentions, shared projections, quality rows and merge records where the entry is the winner. `entity_edges`, `sessions.entity_ids` and merge records where it is the loser have no FK.
- Personal search points are one per surface form.
- The graph has no delete event; only the daily orphan GC removes a node without a row.
- The chat tool's Qdrant fallback answers from the point payload alone.
- ADR 269's `entity_name_rules` was keyed for this ticket.

## Decision

**Delete** is `entity_edit.delete_entry`. It is limited to the owner's personal source row and runs under ADR 266's member lock and OCC `version`. In one Postgres transaction it:
1. writes the block rules;
2. deletes the member's `entity_edges` on the entry (and purges their graph projections);
3. removes the id from the member's `sessions.entity_ids`;
4. deletes the `entity_merges` rows naming the entry as loser;
5. deletes the entities row; everything with an FK cascades, including the household copy;
6. inserts an id-only `entity_deletions` tombstone.

After commit it fires a new `ENTITY_DELETED` event (`fire_background`); the response never waits on Qdrant or the graph. A Core listener, registered in every `GRAPH_MODE`, deletes the Qdrant points by payload and verifies the counts; each arm stamps the tombstone, and a five-minute sweep retries what is unstamped (graph: ten attempts, then the orphan GC owns the node). The graph writer handles the event with a label-scoped `DETACH DELETE` of the personal and projection nodes. The orphan GC remains the backstop. The chat tool's Qdrant fallback now confirms a Postgres row exists before it answers.

**Won't learn** is `action = 'block'` in `entity_name_rules`. Blocks are keyed like retypes, `(user_id, name_key(name), from_type)`. A delete blocks the canonical name and every alias at the entry's kind. It skips any name (canonical or alias) that another live entry of the member's of the same kind uses, and any name a kind correction already sends from that kind elsewhere; both come back in the response as not blocked. It converts each retype rule leading to that kind into a block on its source kind, keeping the retype's target in `restore_to_type`. Extraction therefore makes one decision per extracted entity, on the read kind: block (drop), retype (rewrite) or none. ADR 269's no-chain invariant holds. `apply_name_rules` fails closed: after one retry a rules read error raises `NameRulesUnavailable`. Batch jobs retry with backoff; document ingest clears the file's hash so the next hash-based scan re-reads it; MCP `add_memory` keeps the memory and skips its entities. Blocks are checked on every extraction path, including LLM-chosen relation endpoints in MCP `add_memory`; only explicit MCP `add_entity` / `add_relation` calls bypass them.

- **Undo:** removing a block restores any retype it replaced. The response offers to re-read the documents recorded on the rule at delete time.
- **An explicit naming lifts a block:** a kind correction to K, a rename, or an added alias that gives a live entry of kind K a blocked name removes the block on `(name, K)`. A merge lifts only through its cross-kind correction of the loser's name.

**Hide** is a per-member `entity_hidden(user_id, entity_key)` row. `entity_key` is the viewed row's `published_from` (or its own id), with an FK that cascades when the source is deleted. Every Core read of the household-visible entries a member sees (list and counts, search, related, name lookups, the chat tool, the dashboard, the briefing) and the graph's context-building **seed** selection exclude hidden entries for that member, through one `not_hidden_sql` fragment in `visibility.py` (mirrored in the graph service). A hidden entry can still be named as a graph **neighbour** of another entry in chat context. The detail page shows "Hidden" with "Show again".

One settings page, "Names Lumogis won't learn", lists the member's blocks, kind corrections and hidden entries, and each one can be undone there. Merge undo records have no time expiry: they live while their survivor exists and go when it is deleted.

## Alternatives Considered

- **Graph cleanup by orphan GC only:** up to a day of graph lag after a privacy delete, and the GC guard can refuse personal orphans.
- **Soft delete with a trash period:** Thomas ruled out a trash period, and every read path would need a filter.
- **Separate per-member blocklist table:** duplicates ADR 269's key and needs a cross-table order.
- **Hide as a block rule on the viewer:** changes the viewer's own extraction and is keyed by name, not by entry.
- **Delete through the document-purge orphan path:** that path is document-keyed and skips shared entries; only its Qdrant arm is reused.

Full detail: `.cursor/explorations/LUM-966-entity_delete_wont_learn.md`

## Consequences

- **Easier:** a wrong entry is gone from the page, search, chat and the graph at once, and stays gone after re-reads, including re-reads that find its aliases. Kind corrections, blocks and hidden entries live in one place for the member.
- **Harder:**
  - Every new writer of entity data without an FK (a future split table, new arrays) must be added to `delete_entry`.
  - Every new member-facing entity read must apply the Hide filter (LUM-196's map).
  - LUM-188's account erasure must include `entity_name_rules`, `entity_hidden` and `entity_deletions`.
- **Impossible:**
  - Restoring a deleted entry with its id and manual edits. The way back is a re-read, which creates a new entry.
  - Undoing merges into a deleted entry.
- **Residuals (disclose in the guide and in code):**
  - Summaries, chat memories, briefings and `sessions.entities` still contain the words ("Forget completely", LUM-966 Later).
  - With the graph on, a hidden entry can still be named as a neighbour in graph-sourced chat context (Later).
  - Explicit MCP `add_entity` / `add_relation` bypass blocks (explicit wins, ADR 269).
  - Shared aliases are left unblocked.
- **Retention:** block rules hold names and the ids of the documents that mentioned the entry. They are in the member's export and follow the `entities` lifecycle on account deletion. Tombstones hold ids only.
- **Migration:** `084-entity-delete-hide.sql`, mirrored in `postgres/init.sql`, with a new `ENTITY_DELETED` webhook contract (four coordinated files plus the contract test).

## Revisit conditions

- Members ask to get a deleted entry back as it was → reconsider a short trash period (it conflicts with Decision 1, so Thomas decides).
- Members report hidden entries showing as graph neighbours in chat context → filter ego-expansion neighbours through Postgres.
- Members report that an alias learned from a shared document recreates a deleted entry → block shared aliases too.
- `entity_merges` grows past about 10k rows per member, or a personal-data retention rule is set → add expiry of undo records.
- LUM-188 ships account erasure → route its entity arm through `delete_entry`.
- The graph service runs more than one worker, or webhooks become batched → re-check that `ENTITY_DELETED` ordering against `ENTITY_CREATED` cannot recreate a node. (Answered at implementation: `project_entity` now checks the tombstone when the stored row is missing and writes nothing for a deleted entry.)

## Implementation notes

- Delete, Hide and the settings list: `orchestrator/services/entity_edit.py` (`delete_entry`, `hide_entry`, `unhide_entry`, `list_name_rules`, `remove_name_rule`; `lift_blocks` from `rename_entity` and `change_entity_kind`); routes in `orchestrator/routes/api_v1/kg.py`.
- Rules: `orchestrator/services/entity_name_rules.py` (`record_block`, `remove_block`, `lift_blocks`, `kind_key`, `load_rules`, fail-closed `apply_name_rules`; `record_retype` steps 1a/1b/2a/2b/3).
- After-commit arms and sweep: `orchestrator/services/entity_deletions.py`; scheduled and registered in `orchestrator/main.py`.
- Hide filter: `orchestrator/visibility.py` `not_hidden_sql`, mirrored in `services/lumogis-graph/visibility.py`.
- Graph: `services/lumogis-graph/graph/writer.py` (`on_entity_deleted`, `_entity_deleted`, the `project_entity` and `on_document_shared` guards), `graph/query.py` (context seeds).
- Schema: `postgres/migrations/084-entity-delete-hide.sql`, mirrored in `postgres/init.sql`.
- Web: `EntityDeleteDialog.tsx`, `EntityCard.tsx` (Delete…, Hide), `MeWontLearnView.tsx` (`/me/wont-learn`).
- Proof: `orchestrator/tests/integration/test_entity_delete_live.py` (L1–L11, including the 200-step randomised invariant walk on the real table), `test_graph_entity_delete_falkordb_live.py` (F1–F4), `orchestrator/tests/test_entity_delete.py`, `test_entity_name_rules.py`, `test_entity_deletions.py`, `test_api_v1_kg_delete.py`, `services/lumogis-graph/tests/test_writer_entity_delete_lum966.py`.

## Status history
- 2026-11-17: Draft created by /explore
- 2026-11-17: Revised during /review-plan --arbitrate R1 — Hide scope stated as every Core reader plus graph-context seeds (neighbours Later), matching the plan; blocks also apply to LLM-chosen MCP `add_memory` endpoints; a rename, an added alias or a kind correction lifts a block (merge only via its cross-kind correction). The Decision text had said context-building excludes hidden entries while the plan deferred all of it.
- 2026-11-17: Finalised by /verify-plan. The implementation confirmed the decision. Wording brought in line with what shipped (all agreed at arbitration R1): Qdrant clearing moved off the request path to an `ENTITY_DELETED` listener, the skip rule covers the canonical name and already-corrected names, the fail-closed end states per extraction path, and the `project_entity` revisit question answered by the tombstone guard
