-- Migration 016: per-user connector permissions.
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- Lift connector_permissions and routine_do_tracking from deployment-wide
-- to strict per-(user_id, connector[, action_type]). Closes audit A2.
-- See ADR `per_user_connector_permissions.md` and plan
-- `.cursor/plans/per_user_connector_permissions.plan.md`.
--
-- Mirror conventions: 010 (TEXT id, no FK), 013 (no scope column on these
-- two tables -- they are per-user but not scope-aware), 014/mcp_token_user_map
-- (retain rows on cascade for forensic value).
--
-- Sequencing: assumes 010-015 are live. Idempotent and re-runnable: every
-- DDL uses IF EXISTS / IF NOT EXISTS guards; the eager backfill INSERT
-- uses NOT EXISTS + ON CONFLICT DO NOTHING; the legacy 'default' sweep
-- is a plain DELETE that becomes a no-op after the first apply.
--
-- ROLLBACK NOTE (one-way door): reverting this migration requires manual
-- consolidation of per-user rows into a single global row before the
-- legacy UNIQUE(connector) constraint can be re-added. Operators MUST
-- consult docs/connect-and-verify.md before attempting a downgrade.

BEGIN;

-- ---- Phase 1: connector_permissions constraint swap -----------------------
-- Drop the global UNIQUE(connector). Existing rows already carry user_id
-- (defaulted to 'default' or post-remap to bootstrap admin id by
-- db_default_user_remap.py), so the new composite UNIQUE is satisfied.
ALTER TABLE connector_permissions
    DROP CONSTRAINT IF EXISTS connector_permissions_connector_key;

CREATE UNIQUE INDEX IF NOT EXISTS connector_permissions_user_connector_uniq
    ON connector_permissions (user_id, connector);

CREATE INDEX IF NOT EXISTS connector_permissions_user_idx
    ON connector_permissions (user_id);

-- ---- Phase 2: routine_do_tracking column add + constraint swap -----------
ALTER TABLE routine_do_tracking
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE routine_do_tracking
    DROP CONSTRAINT IF EXISTS routine_do_tracking_connector_action_type_key;

CREATE UNIQUE INDEX IF NOT EXISTS routine_do_tracking_user_connector_action_uniq
    ON routine_do_tracking (user_id, connector, action_type);

CREATE INDEX IF NOT EXISTS routine_do_tracking_user_idx
    ON routine_do_tracking (user_id);

-- ---- Phase 3: eager backfill + sweep (gated on EXISTS users) -------------
-- Fan out legacy 'default'-owned rows to every user that exists in the
-- users table -- INCLUDING disabled users. A disabled user may be
-- re-enabled (services/users.py::set_disabled supports it); their
-- pre-migration DO mode and earned routine elevations must persist
-- across the schema lift. The new application reader queries by the
-- user's real id, so the row MUST exist under their real id post-migration.
--
-- The gate is "EXISTS users" (any row, disabled or not). Single-user
-- deployments where the users table is empty (fresh install,
-- AUTH_ENABLED=false) skip the fan-out -- the 'default' rows remain for
-- db_default_user_remap.py to remap to the bootstrap admin id on the
-- first AUTH_ENABLED=true boot (today's flow exactly).
DO $$
DECLARE
    has_users BOOLEAN;
BEGIN
    SELECT EXISTS (SELECT 1 FROM users) INTO has_users;

    IF has_users THEN
        -- Phase 3a: connector_permissions
        -- Fan out legacy non-ASK 'default' rows to every user (incl.
        -- disabled). ASK rows are skipped because they collapse to the
        -- lazy `_DEFAULT_MODE='ASK'` fallback at read time.
        INSERT INTO connector_permissions (user_id, connector, mode)
        SELECT u.id, p.connector, p.mode
          FROM users u
          CROSS JOIN connector_permissions p
         WHERE p.user_id = 'default'
           AND p.mode != 'ASK'
           AND NOT EXISTS (
             SELECT 1 FROM connector_permissions x
              WHERE x.user_id = u.id AND x.connector = p.connector
           )
        ON CONFLICT (user_id, connector) DO NOTHING;

        -- Sweep ALL legacy 'default' connector rows. ASK rows lose
        -- nothing (the lazy fallback already returns ASK for missing
        -- rows). Non-ASK rows have been fanned out above.
        DELETE FROM connector_permissions WHERE user_id = 'default';

        -- Phase 3b: routine_do_tracking
        -- Fan out only auto_approved=TRUE legacy rows. Already-earned
        -- elevation must persist for every existing user (incl. disabled).
        -- Pending (auto_approved=FALSE) rows are NOT fanned out: pre-
        -- multi-user `approval_count` was contributed by the de-facto
        -- single operator behind 'default'; cross-joining would
        -- over-credit every other user with approvals they did not make.
        INSERT INTO routine_do_tracking
            (user_id, connector, action_type, approval_count, edit_count,
             auto_approved, granted_at, created_at, updated_at)
        SELECT u.id, r.connector, r.action_type, r.approval_count,
               r.edit_count, r.auto_approved, r.granted_at,
               r.created_at, r.updated_at
          FROM users u
          CROSS JOIN routine_do_tracking r
         WHERE r.user_id = 'default'
           AND r.auto_approved = TRUE
           AND NOT EXISTS (
             SELECT 1 FROM routine_do_tracking x
              WHERE x.user_id = u.id
                AND x.connector = r.connector
                AND x.action_type = r.action_type
           )
        ON CONFLICT (user_id, connector, action_type) DO NOTHING;

        -- Sweep ONLY auto_approved=TRUE 'default' rows that have been
        -- fanned out. Pending (auto_approved=FALSE) 'default' rows
        -- remain -- db_default_user_remap.py remaps them to the bootstrap
        -- admin id on the next AUTH_ENABLED=true boot, preserving the
        -- in-progress counter as the bootstrap admin's history.
        DELETE FROM routine_do_tracking
         WHERE user_id = 'default'
           AND auto_approved = TRUE;
    END IF;
    -- has_users = FALSE: legacy 'default' rows untouched. Next
    -- db_default_user_remap.py boot remaps them to the bootstrap admin id.
END $$;

COMMIT;
