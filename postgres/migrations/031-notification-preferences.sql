-- Migration 031: Notification preferences (LUM-93 / ADR 077 chunk 1)
-- Per-user routing prefs, quiet-hours settings, instance tier policy.

CREATE TABLE IF NOT EXISTS notification_user_settings (
    user_id            TEXT NOT NULL DEFAULT 'default',
    timezone           TEXT,
    quiet_hours_start  TIME,
    quiet_hours_end    TIME,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id)
);

CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id            TEXT NOT NULL DEFAULT 'default',
    notification_type  TEXT NOT NULL,
    channel            TEXT NOT NULL,
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, notification_type, channel)
);
CREATE INDEX IF NOT EXISTS ix_notification_prefs_user
    ON notification_preferences (user_id);

CREATE TABLE IF NOT EXISTS notification_tier_policy (
    tier               TEXT PRIMARY KEY,
    bypass_quiet_hours BOOLEAN NOT NULL DEFAULT FALSE,
    default_channels   TEXT[] NOT NULL
);

INSERT INTO notification_tier_policy (tier, bypass_quiet_hours, default_channels) VALUES
  ('urgent', TRUE,  '{ntfy,web_push,in_app}'),
  ('action_required', FALSE, '{ntfy,web_push,in_app}'),
  ('informational', FALSE, '{ntfy,web_push,in_app}'),
  ('background', FALSE, '{in_app}')
ON CONFLICT (tier) DO NOTHING;

-- Rollback: DROP TABLE notification_preferences, notification_user_settings, notification_tier_policy;
