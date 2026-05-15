-- SPDX-License-Identifier: AGPL-3.0-only
-- LUM-39: ntfy upstream 410 / delivery pause metadata on user rows.

ALTER TABLE user_connector_credentials
  ADD COLUMN IF NOT EXISTS delivery_paused BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS delivery_paused_reason TEXT NULL,
  ADD COLUMN IF NOT EXISTS delivery_paused_detail TEXT NULL,
  ADD COLUMN IF NOT EXISTS delivery_paused_at TIMESTAMPTZ NULL;
