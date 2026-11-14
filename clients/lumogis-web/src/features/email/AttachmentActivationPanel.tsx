// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-742 — the zero state IS the activation surface.
//
// `AttachmentIngestToggle` defaults off and never enables itself, deliberately.
// So routing the review list without this panel would show every existing user
// an empty page and nothing to do about it — fixing the invisibility and
// delivering nothing. The empty state therefore carries the per-mailbox toggles
// inline rather than linking to settings.
//
// Three watermark outcomes, not two. `set_attachment_ingest` writes
// `COALESCE(attachment_ingest_since, NOW())` when "include existing" is off, so:
//
//   * first-ever enable        -> watermark = now  -> nothing until new mail
//   * re-enable after disable  -> watermark kept   -> everything since the
//                                                     original opt-in, which is
//                                                     unbounded: attachment rows
//                                                     accrue while filing is off
//   * "include existing"       -> epoch            -> everything ever received
//
// The middle case is deliberate and test-pinned server-side. What is not
// acceptable is the toggle's own copy promising only new mail in that case, so
// this panel states which outcome applies before the user commits.

import type { ApiClient } from "../../api/client";
import type { SourceRow } from "../../api/emailSources";
import { AttachmentIngestToggle } from "./AttachmentIngestToggle";
import { describeActivationOutcome, explainUnavailable } from "./attachmentActivation";

export interface AttachmentActivationPanelProps {
  client: ApiClient;
  /** IMAP mailboxes only — the caller filters. */
  sources: SourceRow[];
  onChanged?: () => void;
}

export function AttachmentActivationPanel({
  client,
  sources,
  onChanged,
}: AttachmentActivationPanelProps): JSX.Element {
  return (
    <section data-testid="attachment-activation">
      <h2>File attachments into your library</h2>
      <p>
        Lumogis can show you the attachments arriving in your mailboxes so you can file
        the ones worth keeping — invoices, letters, forms — without saving and
        re-uploading them by hand. Nothing is filed until you say so.
      </p>
      {sources.length === 0 ? (
        <p data-testid="attachment-activation-empty">
          No mailboxes are connected yet.
        </p>
      ) : (
        <ul>
          {sources.map((src) => {
            const blocked = explainUnavailable(src);
            return (
              <li key={src.id} data-testid={`attachment-activation-${src.id}`}>
                <span>{src.name}</span>
                {blocked ? (
                  <p data-testid={`attachment-activation-blocked-${src.id}`}>{blocked}</p>
                ) : (
                  <>
                    <p data-testid={`attachment-activation-outcome-${src.id}`}>
                      {describeActivationOutcome(src)}
                    </p>
                    <AttachmentIngestToggle
                      client={client}
                      sourceId={src.id}
                      extractionMethod={src.extraction_method ?? ""}
                      enabled={src.attachment_ingest_enabled === true}
                      onChange={() => onChanged?.()}
                    />
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
