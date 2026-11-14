-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-729 follow-up (FP-076) — bound the organize undo sweep.
--
-- `email_cleanup` has had `undo_ticks` + `_UNDO_MAX_TICKS = 20` since LUM-646,
-- with the comment "bound the 'undoing' state so an irreversible message can't
-- wedge a job". The organize undo sweep, written in the same arc, has no
-- equivalent — and its failure is strictly worse than a wedged job.
--
-- `undo_tick` clears `organize_undo_scope` only when NO un-reversed row remains
-- for the scope. A single message that cannot be reversed — deleted upstream,
-- moved by another client, a folder that no longer exists — leaves the scope set
-- forever. And `feed_monitor._run_organize_tick` returns early whenever the undo
-- reports remaining/reversed/failed work:
--
--     undo = email_organize.undo_tick(...)
--     if undo.get("remaining") or undo.get("reversed") or undo.get("failed"):
--         return   # skip reconcile this tick
--
-- So one unreversible message does not merely wedge the undo. It **permanently
-- disables organize for that mailbox** — the standing reconcile sweep never runs
-- again — while opening a fresh IMAP session on every poll tick, forever, to
-- retry the same doomed row. Nothing anywhere says so.
--
--   organize_undo_ticks       -- sweeps spent on the CURRENT undo. Reset when an
--                                undo is requested and when one completes, so it
--                                counts this undo rather than all undos ever.
--   organize_undo_gave_up_at  -- stamped when the bound is hit. Read by the log
--                                route so "we stopped trying" is something the
--                                user can see, not only something in a log file.
--                                A silent give-up would trade one invisible
--                                failure for another.

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS organize_undo_ticks      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS organize_undo_gave_up_at TIMESTAMPTZ;

-- Rollback (manual):
-- ALTER TABLE sources
--     DROP COLUMN organize_undo_ticks,
--     DROP COLUMN organize_undo_gave_up_at;
