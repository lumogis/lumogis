-- Migration 080: bound the typed entity_edges reconcile replay (LUM-599) and
-- index its stale scan (LUM-600). Both follow up LUM-580 (migration 049).
--
-- reconcile_attempts (LUM-599)
-- ----------------------------
-- `reconcile_entity_edges` (services/lumogis-graph/graph/reconcile.py) replays
-- every active, unstamped typed edge on every pass. An edge whose endpoint node
-- never materialises in FalkorDB (deleted entity, an entity that stays staged)
-- used to be retried forever. The pass now increments this counter each time a
-- replay runs but does not attach (a node is missing). It stops scanning a row
-- once the counter reaches GRAPH_RECONCILE_EDGE_MAX_ATTEMPTS, and counts those
-- rows as `aged_out`.
--
-- The counter goes back to 0 whenever the edge gets a fresh chance:
--   * a confirmed attach (live `stamp_edge_projected` or the reconcile stamp);
--   * an entity merge that repoints the edge (graph_projected_at -> NULL);
--   * a staged entity at either end being promoted.
--
-- It counts real misses only. A FalkorDB error, a disabled graph or a row the
-- pass never reached (limit_per_type) does not increment it, so an outage or a
-- large backlog cannot age a healthy edge out.
--
-- NOT NULL DEFAULT 0 is a metadata-only ALTER on Postgres 11+ (no table
-- rewrite). Existing rows read 0, so every pending edge keeps its full budget.
--
-- idx_entity_edges_reconcile_stale (LUM-600)
-- ------------------------------------------
-- At steady state almost every row is stamped, so a partial index on the
-- unstamped rows is near-empty, and the stale scan stops being a sequential
-- scan that grows with the table. The predicate repeats the scan's WHERE
-- clauses word for word, so the planner can prove the scan is covered. The key
-- is (reconcile_attempts, id):
--   * `reconcile_attempts < N` is an index range, so aged-out orphans still in
--     the index are never visited by the scan;
--   * the scan's `ORDER BY reconcile_attempts, id` reads straight off the
--     index, putting fresh misses first under a LIMIT.
-- N is not in the predicate: it is configurable, and an index whose predicate
-- named it would stop matching when an operator changed it.
--
-- Idempotent (IF NOT EXISTS), additive, forward-only — safe on existing volumes.

ALTER TABLE entity_edges ADD COLUMN IF NOT EXISTS reconcile_attempts INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_entity_edges_reconcile_stale
    ON entity_edges (reconcile_attempts, id)
    WHERE graph_projected_at IS NULL
      AND valid_until IS NULL
      AND relation_type <> 'RELATES_TO';
