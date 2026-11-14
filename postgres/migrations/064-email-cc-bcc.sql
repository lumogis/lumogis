-- Migration 064: CC/BCC recipients on the outbound send audit (LUM-698).
--
-- `email_sent_log` (migration 062) recorded only `to_addrs`. Adding CC/BCC to
-- compose/reply/forward means the audit trail must keep the three DISTINCT —
-- flattening everyone into `to_addrs` would both lie about what the user sent
-- and (worse) put BCC recipients into the set the first-contact check queries.
--
-- First-contact participation (LUM-698 decision, deliberate and asymmetric):
--
--   * `cc_addrs` DOES participate. `_is_first_contact`'s sent-side membership
--     query becomes `unnest(to_addrs || cc_addrs)`: if you CC'd someone, you
--     demonstrably corresponded with them, exactly as if you had put them in To.
--
--   * `bcc_addrs` NEVER participates, and gets NO index. BCC is stored for the
--     owner's own audit trail only. Querying it would turn the Ask/Do gate into
--     a side channel over addresses the user deliberately kept private. The
--     accepted cost: re-sending to an address you have only ever BCC'd forces
--     Ask every time, forever. That is fail-closed and intended — do NOT "fix"
--     it later by adding bcc_addrs to the membership query.
--
-- The cc index mirrors the shipped `idx_email_sent_log_to` exactly (same shape,
-- same purpose). Note honestly that neither is load-bearing for the CURRENT
-- `unnest(...)`-form query, which the planner may satisfy by scan; both exist so
-- a future rewrite to the `&&` overlap form has them.
--
-- `email_sent_log.action` is free TEXT with no CHECK constraint (migration 062),
-- so the new 'forward' value needs no constraint change here.
--
-- Existing rows take the '{}' defaults — no backfill required.
--
-- Manual downgrade (operator-only; the runner is apply-only / filename-tracked):
--   DROP INDEX IF EXISTS idx_email_sent_log_cc;
--   ALTER TABLE email_sent_log DROP COLUMN cc_addrs, DROP COLUMN bcc_addrs;
--
-- Idempotent.

BEGIN;

ALTER TABLE email_sent_log
    ADD COLUMN IF NOT EXISTS cc_addrs  TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS bcc_addrs TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN email_sent_log.cc_addrs IS
  'Normalized CC addr-specs. Participates in the first-contact membership test '
  '(unnest(to_addrs || cc_addrs)) — a past CC counts as contacted.';

COMMENT ON COLUMN email_sent_log.bcc_addrs IS
  'Normalized BCC addr-specs. AUDIT ONLY — deliberately NOT indexed and NEVER '
  'queried by the first-contact membership test, so BCC cannot become a side '
  'channel. Re-sending to a BCC-only address forces Ask every time (intended).';

-- Mirrors idx_email_sent_log_to. No bcc counterpart, by the decision above.
CREATE INDEX IF NOT EXISTS idx_email_sent_log_cc
    ON email_sent_log USING GIN (cc_addrs);

COMMIT;
