-- LUM-690 / LUM-633: per-source poll health so a mailbox that authenticated at
-- connect time but can no longer poll (revoked/expired OAuth grant, a soft-saved
-- OAuth connect on an egress-proxied deployment whose first real sync then
-- fails, or a broken app-password credential) is visible to the user instead of
-- silently logging `email_poll_skip code=credential` forever with the mailbox
-- still showing as connected.
--
-- Scope: set only at the EmailImapSource construction site (feed_monitor's two
-- `except Exception` blocks around `EmailImapSource(source)`) — i.e. "this
-- mailbox cannot currently authenticate", not every transient mid-poll hiccup
-- (a single malformed message, a network blip during fetch). Cleared back to
-- NULL the next time construction succeeds (self-healing on reconnect).
ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS last_poll_error_code TEXT,        -- 'credential' | NULL = healthy
    ADD COLUMN IF NOT EXISTS last_poll_error_at    TIMESTAMPTZ; -- when first/last observed

-- Rollback (manual DDL; the migration runner is apply-only / filename-tracked):
-- ALTER TABLE sources
--     DROP COLUMN last_poll_error_code,
--     DROP COLUMN last_poll_error_at;
