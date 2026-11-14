-- LUM-643: durable, Lumogis-owned, per-user triage state on each signal.
--   needs_you / good_to_know / snoozed(until T) / done — decoupled from the
--   mailbox's read-state ("the mailbox forgets; Lumogis remembers"). Reading
--   never changes state; the user defers/completes explicitly. Snooze re-surfaces
--   at query time (no scheduler): snoozed_until <= now() → treated as needs_you.
--
-- Per-(user, signal): each household member triages a shared signal independently
-- (a shared signal is one projected row seen by multiple members under different
-- user_ids). ON DELETE CASCADE cleans up when the signal is purged / unprojected.
--
-- Additive + idempotent. Reminder DELIVERY (the snooze-expiry ping / morning
-- brief) is LUM-648, not here — but the index below is shaped for its expiry scan
-- (user + status + due time) so it ships once.
CREATE TABLE IF NOT EXISTS signal_triage (
    user_id       TEXT NOT NULL DEFAULT 'default',
    signal_id     UUID NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    status        TEXT NOT NULL CHECK (status IN ('needs_you','good_to_know','snoozed','done')),
    snoozed_until TIMESTAMPTZ,   -- non-null iff status='snoozed'
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, signal_id),
    -- Enforce the pairing at the DB, not just in the service, so no other writer
    -- (LUM-648, admin SQL) can violate the invariant LUM-648 reads.
    CONSTRAINT snoozed_until_pairing CHECK ((status = 'snoozed') = (snoozed_until IS NOT NULL))
);

-- Shaped for LUM-648's expiry scan (user + status + due time).
CREATE INDEX IF NOT EXISTS idx_signal_triage_user_status_due
    ON signal_triage (user_id, status, snoozed_until);

-- Rollback: DROP TABLE IF EXISTS signal_triage;
