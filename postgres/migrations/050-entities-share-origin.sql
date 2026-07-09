-- Migration 050: entities.share_origin provenance for graph-aware sharing (LUM-586).
--
-- When a personal entity is projected into shared scope it can arrive via two
-- independent paths:
--   * a direct LUM-581 entity share (the user shared the entity itself), or
--   * a LUM-586 document cascade (the entity was extracted from a shared doc).
-- Both land on the SAME shared projection row (deterministic uuid5 PK, partial
-- unique index on (published_from, scope)). `share_origin` records which
-- path(s) justified the projection so refcounted retraction can decide, on a
-- document unshare/purge, whether the shared entity is still justified:
--
--   'document' = doc-cascade only  -> retract when the last shared source doc
--                                     that mentions it is unshared/purged.
--   'user'     = direct share only -> never auto-retracted by a document unshare.
--   'multiple' = both              -> a doc unshare downgrades to 'user' (never
--                                     deletes); a direct unshare downgrades to
--                                     'document' (handled by LUM-581 path).
--
-- NULL is the pre-migration / scope='personal' value. Pre-migration shared rows
-- (NULL) are treated as 'user' by the retraction planner — i.e. never
-- auto-retracted by a document unshare — because we cannot prove they came from
-- a document cascade.
--
-- Additive, nullable, no default, forward-only (IF NOT EXISTS) — safe on
-- existing volumes.

ALTER TABLE entities ADD COLUMN IF NOT EXISTS share_origin TEXT;

COMMENT ON COLUMN entities.share_origin IS
  'Provenance of a scope=shared entity projection: document | user | multiple. '
  'Drives refcounted retraction (LUM-586). NULL for scope=personal rows and '
  'pre-migration shared rows (treated as user by the retraction planner).';
