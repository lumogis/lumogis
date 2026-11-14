// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-742 — moving between the two email routes.
//
// Two items, not three. "Filed" is a section inside Attachments rather than a
// peer: a third tab would make the history look as important as the pending
// queue, and it is not — you look at pending often and at history only when
// something seems missing.
//
// The count is shown only when it is non-zero. A badge is the quietest
// notification there is and it should be earned; a permanent "0" on an opt-in
// filing queue is an attention tax that buys nothing. Discovery is the empty
// state's job (see `AttachmentActivationPanel`); the badge's job is recurrence.

import { useNavigate } from "react-router-dom";

import { SegmentedControl } from "../../components/SegmentedControl";

export interface EmailTabsProps {
  active: "triage" | "attachments";
  /** Pending attachments across every mailbox. Omit or pass 0 to hide it. */
  pendingCount?: number;
}

export function EmailTabs({ active, pendingCount = 0 }: EmailTabsProps): JSX.Element {
  const navigate = useNavigate();
  return (
    <SegmentedControl
      className="lumogis-email-tabs"
      ariaLabel="Email views"
      value={active}
      onChange={(v) => navigate(v === "triage" ? "/email" : "/email/attachments")}
      options={[
        { value: "triage", label: "Triage" },
        {
          value: "attachments",
          label: pendingCount > 0 ? `Attachments (${pendingCount})` : "Attachments",
        },
      ]}
    />
  );
}
