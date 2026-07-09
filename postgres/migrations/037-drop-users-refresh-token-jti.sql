-- Migration 037: drop legacy users.refresh_token_jti (LUM-244 — closes the
-- LUM-29 / ADR 041 dual-write downgrade window).
--
-- `refresh_token_jti` held the single-active refresh-JWT jti per user (the v1
-- contract from migration 010). LUM-29 (ADR 041) replaced it with the
-- `auth_sessions` table + `users.token_version` (migration 023), shipped in
-- 0.4.0 (2026-05-15). ADR 041 retained the column for one release of downgrade
-- safety; six releases have shipped since (0.5.0 → 0.8.0) and a CI grep gate has
-- guaranteed no production code reads or writes it. The downgrade window is long
-- closed, so the column is safe to drop. Idempotent (IF EXISTS).

ALTER TABLE users DROP COLUMN IF EXISTS refresh_token_jti;
