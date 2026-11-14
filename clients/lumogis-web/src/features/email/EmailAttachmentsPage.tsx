// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-742 — `/email/attachments`.
//
// Cross-mailbox by default. The review list and the filed list both take an
// optional `sourceId`, which is not incidental: their authors made the
// cross-mailbox surfaces cross-mailbox and the per-mailbox ones per-mailbox.
// The toggle and the rules list require a source and live on the mailbox card.
//
// This page owns two things and deliberately not a third: the candidates query
// and the activation/review branch. The paperless advisory belongs to
// `AttachmentReviewList`, which already held it — splitting ownership between a
// page and its child is how one of them ends up rendering stale text.

import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { fetchCandidates } from "../../api/emailAttachmentIngest";
import { listSources } from "../../api/emailSources";
import { AttachmentActivationPanel } from "./AttachmentActivationPanel";
import { AttachmentReviewList } from "./AttachmentReviewList";
import { EmailTabs } from "./EmailTabs";
import { FiledFromEmailList } from "./FiledFromEmailList";

export function EmailAttachmentsPage(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();

  // The SAME key the nav gate uses (`App.tsx`) and `EmailSignalsView` reads.
  // Enabling a mailbox has to update the activation branch, the settings page
  // and the nav together; a private key here would leave two of them stale.
  const sourcesQ = useQuery({
    queryKey: ["email", "sources"],
    queryFn: () => listSources(client),
  });
  const candidatesQ = useQuery({
    queryKey: ["email", "attachment-candidates"],
    queryFn: () => fetchCandidates(client, {}),
  });

  const imapSources = useMemo(
    () => (sourcesQ.data?.sources ?? []).filter((s) => s.source_type === "imap"),
    [sourcesQ.data],
  );
  const anyEnabled = imapSources.some((s) => s.attachment_ingest_enabled === true);

  // No mailbox at all: `/email` is already hidden by the nav gate, but the route
  // stays directly addressable. Send them where the shell's own default sends
  // an unknown path rather than rendering a panel with nothing to activate.
  if (sourcesQ.isSuccess && imapSources.length === 0) {
    return <Navigate to="/chat" replace />;
  }

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["email", "sources"] });
    void qc.invalidateQueries({ queryKey: ["email", "attachment-candidates"] });
  };

  return (
    <div className="lumogis-email-attachments">
      <EmailTabs active="attachments" pendingCount={candidatesQ.data?.pending_total ?? 0} />

      {!anyEnabled ? (
        <AttachmentActivationPanel
          client={client}
          sources={imapSources}
          onChanged={refresh}
        />
      ) : (
        <>
          <AttachmentReviewList client={client} onDecided={refresh} />

          {/* Which mailboxes have more than this page could show. The whole
              user-facing mitigation for a mailbox not being fully represented:
              without it, "your window filled up" is indistinguishable from
              "nothing pending here". */}
          {(candidatesQ.data?.truncated_sources ?? []).length > 0 ? (
            <p data-testid="attachments-truncated-sources">
              {`More attachments waiting in ${(candidatesQ.data?.truncated_sources ?? [])
                .map((s) => s.source_name || s.source_id)
                .join(", ")}`}
            </p>
          ) : null}
          {(candidatesQ.data?.errored_sources ?? []).length > 0 ? (
            <p role="alert" data-testid="attachments-errored-sources">
              {`Couldn't check ${(candidatesQ.data?.errored_sources ?? [])
                .map((s) => s.source_name || s.source_id)
                .join(", ")} just now — the rest of the list is still accurate.`}
            </p>
          ) : null}

          <section data-testid="attachments-filed">
            <h2>Filed from email</h2>
            <FiledFromEmailList client={client} />
          </section>
        </>
      )}
    </div>
  );
}
