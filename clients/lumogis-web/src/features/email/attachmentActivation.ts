// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-742 — what enabling a mailbox will actually do, in the user's terms.
//
// Separate from the panel so it is testable without rendering, and so the panel
// file exports only a component.

import type { SourceRow } from "../../api/emailSources";

/** Which of the three watermark outcomes applies to this mailbox.
 *
 *  `set_attachment_ingest` writes `COALESCE(attachment_ingest_since, NOW())`
 *  when "include existing" is off, so a mailbox that was enabled before keeps
 *  its original watermark and re-enabling surfaces everything accumulated since
 *  — which is unbounded, because attachment rows accrue while filing is off.
 *  Saying "new attachments will appear" in that case is simply false. */
export function describeActivationOutcome(src: SourceRow): string {
  if (src.attachment_ingest_since) {
    return (
      "This mailbox was set up for filing before, so turning it back on brings " +
      "back everything that has arrived since — which may be a lot."
    );
  }
  return "Attachments that arrive from now on will appear here for you to decide on.";
}

/** Why this mailbox cannot be switched on yet, or `null` when it can. */
export function explainUnavailable(src: SourceRow): string | null {
  if (src.extraction_method !== "imap_full_ingest") {
    return (
      "This mailbox only syncs message details, not the messages themselves, " +
      "so there are no attachments to review. Switch it to full ingest first."
    );
  }
  // Full ingest is SET but not in effect — the operator flag is off. The toggle
  // does not catch this, because the mode is right; enabling would simply never
  // produce a candidate. That is the silent no-op the panel exists to prevent.
  if (src.full_ingest_active === false) {
    return (
      "Full ingest is selected for this mailbox but not switched on for this " +
      "Lumogis yet — ask your operator to enable it, then come back."
    );
  }
  return null;
}
