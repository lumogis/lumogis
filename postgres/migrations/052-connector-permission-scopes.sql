-- Migration 052: connector_permissions.scopes for capability permission grants (LUM-612).
--
-- LUM-507 pillar (a). Extends the per-user Ask/Do permission row (migration 016)
-- with the ADR-024-reserved `scopes TEXT[]` grant set. A capability declares
-- `permissions_required` (real since LUM-41/ADR-169); Core enforces those required
-- scopes against this granted set at the invocation chokepoint (least-privilege,
-- fail-closed). The connector for a capability is `capability.{manifest.id}`.
--
-- Additive and non-breaking: existing binary ASK/DO rows get the empty default
-- `'{}'` (no scopes granted = no scoped capability action authorised), so their
-- behaviour is unchanged. A constant DEFAULT is metadata-only on modern Postgres
-- (no table rewrite).
--
-- This is NOT the memory-visibility `scope` (personal/shared/system) used by
-- `visible_filter`; these are permission grant scopes (`area:verb`, e.g.
-- `memory:read`). See permissions.py.

BEGIN;

ALTER TABLE connector_permissions
  ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{}';

COMMIT;
