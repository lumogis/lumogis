// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-652: the confirm step of the send flow. Presentational — the parent
// (EmailComposer) owns the proposal lifecycle and passes status + callbacks.
// "Send this to X, from Work — alice@acme.com?" with the first-contact notice, [Send]
// (primary) / [Save to Drafts] (secondary), and live Sending→Sent✓/Failed with a
// Retry + Save-to-Drafts fallback that never loses the composed text.

import { formatBytes } from "../../api/emailAttachments";

export type SendStatus = "idle" | "sending" | "sent" | "failed";

export interface SendConfirmCardProps {
  to: string;
  /** LUM-698: every recipient of an irreversible send must be visible at the
   * approval step — not just `to`. */
  cc?: string[];
  bcc?: string[];
  /** LUM-698: which flow this confirms. Drives the prompt + aria copy so a
   * forward is never described as a reply. */
  mode?: "reply" | "compose" | "forward";
  /** The account the message is sent from — `Name — address` when the address
   * resolved, the mailbox name when it did not (LUM-831). Every caller supplies
   * one or the other; the `"this mailbox"` fallback below is defensive only and
   * seeing it means the host wired neither. */
  fromAddress?: string | null;
  subject: string;
  body: string;
  firstContact: boolean;
  /** LUM-695: how many files ride along, and how big they are in total. The
   * user must SEE that they are about to send files — and that is only true if
   * the card says so. Filenames are deliberately absent: the backend annotation
   * carries approval-relevant scalars only. */
  attachmentCount?: number;
  attachmentBytes?: number;
  status: SendStatus;
  /** True when the succeeded action was Save-to-Drafts (not a real send) — the
   * confirmation must not read "Sent ✓" for a draft the user still has to send. */
  savedDraft?: boolean;
  /** Present on a failed result — drives the reactive degrade + message. */
  failureCode?: string | null;
  failureMessage?: string | null;
  onSend: () => void;
  onSaveDraft: () => void;
  /** Back to editing (keeps the composed text). */
  onBack: () => void;
}

export function SendConfirmCard({
  to,
  cc,
  bcc,
  mode = "compose",
  fromAddress,
  subject,
  body,
  firstContact,
  attachmentCount,
  attachmentBytes,
  status,
  savedDraft,
  failureCode,
  failureMessage,
  onSend,
  onSaveDraft,
  onBack,
}: SendConfirmCardProps): JSX.Element {
  // LUM-831: "your address" is gone. Every caller now supplies either the
  // resolved sending address or the mailbox NAME, so the last screen before
  // an irreversible send names the account it leaves from. The remaining
  // fallback is defensive only and is deliberately a real referent rather
  // than the old placeholder — but if you are seeing it, the host failed to
  // pass both `fromAddress` and `fromName`, which is a wiring bug.
  const fromLabel = (fromAddress ?? "").trim() || "this mailbox";
  const ccList = (cc ?? []).filter((a) => a.trim());
  const bccList = (bcc ?? []).filter((a) => a.trim());
  const verb = mode === "forward" ? "Forward" : "Send";
  const busy = status === "sending";
  // No Drafts folder on this mailbox → hide Save-to-Drafts, keep Send available.
  const saveDraftUnavailable = failureCode === "no_drafts_folder";

  return (
    <div className="lumogis-send-confirm" role="group" aria-label="Confirm send">
      <p className="lumogis-send-confirm__prompt">
        {verb} this to <strong>{to}</strong>, from <strong>{fromLabel}</strong>?
      </p>

      {/* LUM-698: cc/bcc are real recipients of an irreversible send — showing
          only `to` would under-represent who receives this. */}
      {ccList.length > 0 ? (
        <p className="lumogis-send-confirm__recipients" data-testid="confirm-cc">
          Cc: <strong>{ccList.join(", ")}</strong>
        </p>
      ) : null}
      {bccList.length > 0 ? (
        <p className="lumogis-send-confirm__recipients" data-testid="confirm-bcc">
          Bcc: <strong>{bccList.join(", ")}</strong>
        </p>
      ) : null}

      {firstContact ? (
        <p className="lumogis-send-confirm__first-contact" role="note">
          {/* Deliberately recipient-AGNOSTIC (LUM-698). The flag can be raised by
              any recipient incl. a cc/bcc, so naming `to` actively misattributed
              it. The backend returns only a boolean by design (_annotate_send
              never surfaces the address — it runs before the ownership gate), so
              naming the real one would mean weakening that contract. */}
          One or more recipients haven’t been emailed from this mailbox before.
        </p>
      ) : null}

      {/* LUM-695: sending a file is not something to discover after the fact. */}
      {(attachmentCount ?? 0) > 0 ? (
        <p className="lumogis-send-confirm__attachments" data-testid="confirm-attachments">
          {attachmentCount} {attachmentCount === 1 ? "file" : "files"}
          {attachmentBytes ? `, ${formatBytes(attachmentBytes)}` : ""} attached
        </p>
      ) : null}

      <div className="lumogis-send-confirm__preview">
        <div className="lumogis-send-confirm__subject">{subject}</div>
        {/* The exact body is re-displayed — the read-checkpoint before sending.
            LUM-698: this is the COMPOSED body (note + quoted original), threaded
            in by the parent, so the preview cannot drift from the payload. */}
        <pre className="lumogis-send-confirm__body">{body}</pre>
      </div>

      {status === "sent" ? (
        <p className="lumogis-send-confirm__status" role="status">
          {savedDraft ? "Saved to Drafts ✓" : "Sent ✓"}
        </p>
      ) : null}

      {status === "failed" ? (
        <p className="lumogis-send-confirm__status lumogis-send-confirm__status--error" role="alert">
          {saveDraftUnavailable
            ? "This mailbox has no Drafts folder."
            : `Couldn’t send${failureMessage ? `: ${failureMessage}` : "."}`}
        </p>
      ) : null}

      {status === "sending" ? (
        <p className="lumogis-send-confirm__status" role="status">
          Sending…
        </p>
      ) : null}

      {status !== "sent" ? (
        <div className="lumogis-send-confirm__actions">
          <button
            type="button"
            className="lumogis-send-confirm__send"
            disabled={busy}
            onClick={onSend}
          >
            {status === "failed" ? "Retry" : "Send"}
          </button>
          {!saveDraftUnavailable ? (
            <button
              type="button"
              className="lumogis-send-confirm__save-draft"
              disabled={busy}
              onClick={onSaveDraft}
            >
              Save to Drafts
            </button>
          ) : null}
          <button
            type="button"
            className="lumogis-send-confirm__back"
            disabled={busy}
            onClick={onBack}
          >
            Back
          </button>
        </div>
      ) : null}
    </div>
  );
}
