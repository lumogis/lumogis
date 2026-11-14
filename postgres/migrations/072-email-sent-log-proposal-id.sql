-- Migration 072: link email_sent_log rows to the proposal that produced them (LUM-793).
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- WHY: a failed send is now terminal (LUM-762 / ADR-201) and LUM-793 surfaces those
-- rows for recovery. Before offering "Save to Drafts" the surface must answer a
-- question the proposal row cannot: did the message actually go out?
--
-- `_record_sent_log` already writes at SUBMISSION time, inside the handler, right
-- after `sender.send()` returns and long before `mark_done` — so a sent-log row
-- exists even for a proposal that was later stranded as `stale_executing_claim`.
-- The evidence is there; what was missing is the link.
--
-- Without this column the match is heuristic (user + source + subject + recipients
-- within a window of `finished_at`), which mis-fires when the same subject goes to
-- the same person twice in the window. The failure mode of that mis-fire is telling
-- someone their email was sent when it was not — the worst error this feature can
-- make. One nullable column removes the guesswork.
--
-- Additive and nullable: rows written before this migration keep working through
-- the heuristic path, and the first-contact query (`smtp_send.py` `_is_first_contact`)
-- reads `to_addrs` only, so it is unaffected.
--
-- Manual downgrade: DROP INDEX idx_email_sent_log_proposal;
--                   ALTER TABLE email_sent_log DROP COLUMN proposal_id;
--
-- Idempotent.

BEGIN;

ALTER TABLE email_sent_log
    ADD COLUMN IF NOT EXISTS proposal_id BIGINT NULL;

-- Partial: only recovery lookups use it, and only non-NULL rows can be looked up.
-- No FK to action_proposals — the sent log is an audit record of what left the
-- building and must outlive any proposal-row cleanup.
CREATE INDEX IF NOT EXISTS idx_email_sent_log_proposal
    ON email_sent_log (proposal_id)
    WHERE proposal_id IS NOT NULL;

COMMENT ON COLUMN email_sent_log.proposal_id IS
    'LUM-793: the action_proposals row this send came from, or NULL for a send '
    'made before migration 072 or outside the proposal path. Written at '
    'SUBMISSION time by _record_sent_log, so it exists even for a proposal that '
    'was later stranded — which is what lets the recovery surface say whether a '
    'failed send actually went out. NOT a foreign key: the sent log is an audit '
    'record of what left the building and must outlive proposal cleanup.';

-- The table comment written by migration 066 became FALSE when LUM-793 gave
-- terminal proposals a grace period. Left alone it would tell the next reader
-- the opposite of what the sweep does.
COMMENT ON TABLE email_attachment_staged IS
    'Outbound staging index (LUM-695). Swept by PROPOSAL STATE, not wall clock: '
    'action_proposals rows never expire, so a file whose proposal is still '
    'pending/approved/claimed/executing is NEVER swept however old. An '
    'UNREFERENCED row (proposal_id IS NULL — an abandoned composer) ages out on '
    'EMAIL_ATTACHMENT_STAGING_TTL_HOURS, and a terminal proposal '
    '(done/rejected/archived/dead) is swept EMAIL_ATTACHMENT_DEAD_GRACE_HOURS '
    'after its finished_at (LUM-793: 7 days, so a failed send listed for '
    'recovery still has its files).';

COMMIT;
