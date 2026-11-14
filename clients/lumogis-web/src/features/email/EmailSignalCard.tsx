// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-642: rich rendering of one classified email signal — sender, summary,
// category, time, importance. Data all comes from GET /signals.
// LUM-643: durable triage actions (Done / Snooze / Not important / Reopen). The
// card stays HOOK-FREE — it's a pure <li> that receives named callbacks; all
// mutation state lives in EmailInboxView (so hook count never varies per render).

import { useCallback, useState } from "react";

import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import { listAttachments, type AttachmentRow } from "../../api/emailAttachments";
import {
  fetchEmailBody,
  type EmailBodyDisplayReason,
  type EmailInlineImage,
} from "../../api/emailSend";
import type { EmailSignalRow, TriageStatus } from "../../api/emailSources";
import { AttachmentList } from "./AttachmentList";
import { EmailBody } from "./EmailBody";
import { NEEDS_YOU_IMPORTANCE } from "./lanes";
import { SNOOZE_PRESETS } from "./triage";

/** What the card holds for the body: the wire response, or a client-synthesised
 * failure state. Same shape either way, so the view does not care which. */
interface EmailBodyDisplayState {
  body: string;
  truncated: boolean;
  degraded: boolean;
  reason: EmailBodyDisplayReason;
  /** LUM-713 — carried on the same response as the text, so a failure state
   *  simply has none rather than needing a second failure mode of its own. */
  inline_images: EmailInlineImage[];
  inline_images_truncated: boolean;
}

/** An HTTP failure, mapped to what the reader should actually be told.
 *
 * `degraded` drives the Try again button, so it must be true only where a
 * retry can plausibly work: not for a 429 the retry would immediately trip
 * again, and not for a 404 that will never resolve.
 */
function bodyErrorState(err: unknown): EmailBodyDisplayState {
  const status = err instanceof ApiError ? err.status : 0;
  const empty = { body: "", truncated: false, inline_images: [], inline_images_truncated: false };
  if (status === 429) {
    return { ...empty, degraded: false, reason: "rate_limited" };
  }
  if (status === 404 || status === 403) {
    return { ...empty, degraded: false, reason: "unavailable" };
  }
  // Network failure, 5xx, or anything else unmodelled: retryable by default.
  return { ...empty, degraded: true, reason: "mailbox_unreachable" };
}

function senderLabel(signal: EmailSignalRow): string {
  const name = (signal.from_display ?? "").trim();
  const addr = (signal.from_addr ?? "").trim();
  return name || addr || "Unknown sender";
}

function timeLabel(signal: EmailSignalRow): string {
  const ts = signal.published_at ?? signal.created_at ?? null;
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export interface EmailSignalCardProps {
  signal: EmailSignalRow;
  /** Set a durable triage status (Done / Not important / a snooze preset). */
  onSetTriage: (signalId: string, status: TriageStatus, snoozedUntil?: string) => void;
  /** Clear the triage row → back to default lane derivation (Reopen / Un-snooze). */
  onClearTriage: (signalId: string) => void;
  /** LUM-649: record 👍/👎 relevance feedback (positive=true promotes). */
  onFeedback: (signalId: string, positive: boolean) => void;
  /** LUM-652: open the composer to reply to this email. Offered only when the
   * signal has a sender to reply to (the server raises no_reply_to otherwise). */
  onReply?: (signal: EmailSignalRow) => void;
  /** LUM-705: open the composer to reply to everyone on the thread. Offered on
   * the SAME condition as Reply (a sender to reply to), not on "did the original
   * have other recipients?" — answering that needs the header fetch, which would
   * then fire on every card render. A reply-all with an empty Cc is a reply. */
  onReplyAll?: (signal: EmailSignalRow) => void;
  /** LUM-698: open the composer to forward this email. Unlike Reply this needs
   * NO `from_addr` — you choose the recipients, so a message whose sender failed
   * to parse is still forwardable. */
  onForward?: (signal: EmailSignalRow) => void;
  /** LUM-821: which mailbox this message arrived in, or `""` to show nothing.
   *
   * The lanes are cross-mailbox (ADR — unified read, per-mailbox act), so on a
   * multi-mailbox account a card gives no clue which account it belongs to.
   * That is the failure mode unified inboxes are criticised for: you reply from
   * the wrong address because nothing told you which one you were reading.
   *
   * The HOST decides whether to pass anything — see `mailboxLabelFor` in
   * `EmailInboxView`. The card deliberately does not read `signal.source_label`
   * itself: whether to show it depends on how many mailboxes the viewer has and
   * which they own, and neither is knowable from one row. Defaults to absent, so
   * a host that does not pass it is unchanged. */
  mailboxLabel?: string;
  /** True once this card's mutation is in flight (buttons disabled). */
  pending?: boolean;
  /** LUM-695: when supplied, the card offers an Attachments disclosure that
   * fetches metadata on FIRST OPEN. Deliberately lazy — an inbox renders many
   * cards, and fetching every one eagerly would be a request per message for
   * data most users never look at. Omit it and the card is unchanged. */
  attachmentClient?: ApiClient;
  /** LUM-719: false when this signal lives in a mailbox the viewer does NOT own —
   * a household-shared email from another member's account. Sending needs that
   * mailbox's SMTP credentials, which the viewer has none of, so Reply / Reply all
   * / Forward are hidden rather than offered and then failing. Defaults to true so
   * a host that doesn't know stays on the previous behaviour. */
  sendable?: boolean;
  /** LUM-810: false when the viewer does not own this signal's mailbox, so the
   * body can never be fetched for them (the read route 404s — see LUM-719 for
   * why an affordance that cannot succeed is worse than none).
   *
   * A SEPARATE prop from `sendable` even though both are computed from the same
   * ownership predicate today: "can act on this mailbox" and "can read from it"
   * are different questions, and collapsing them would make a future change to
   * one silently change the other. Defaults to true, like `sendable`. */
  readable?: boolean;
  /** LUM-810: when supplied, the card offers a Message disclosure that fetches
   * the body on FIRST OPEN. Lazy for the same reason attachments are — an inbox
   * renders many cards and each body is a live IMAP fetch. */
  bodyClient?: ApiClient;
  /** LUM-696: true when this row is in the multi-select. */
  selected?: boolean;
  /** LUM-696: when supplied, the card renders a leading selection checkbox.
   * `shiftKey` distinguishes a range extend from a plain toggle. Omit it and the
   * card renders exactly as before — every pre-LUM-696 test still holds. */
  onToggleSelect?: (signalId: string, shiftKey: boolean) => void;
}

export function EmailSignalCard({
  signal,
  onSetTriage,
  onClearTriage,
  onFeedback,
  onReply,
  onReplyAll,
  onForward,
  mailboxLabel = "",
  pending = false,
  attachmentClient,
  sendable = true,
  readable = true,
  bodyClient,
  selected = false,
  onToggleSelect,
}: EmailSignalCardProps): JSX.Element {
  const [attachments, setAttachments] = useState<AttachmentRow[] | null>(null);
  const [attachmentsAvailable, setAttachmentsAvailable] = useState(true);
  const [attachmentsLoading, setAttachmentsLoading] = useState(false);
  const [attachmentsError, setAttachmentsError] = useState<string | null>(null);

  const loadAttachments = useCallback(async () => {
    if (!attachmentClient || attachments !== null || attachmentsLoading) return;
    setAttachmentsLoading(true);
    setAttachmentsError(null);
    try {
      const res = await listAttachments(attachmentClient, signal.signal_id);
      setAttachments(res.rows);
      setAttachmentsAvailable(res.available);
    } catch {
      setAttachmentsError("failed");
    } finally {
      setAttachmentsLoading(false);
    }
  }, [attachmentClient, attachments, attachmentsLoading, signal.signal_id]);

  // LUM-810 — the body, fetched on first open of the Message disclosure.
  const [body, setBody] = useState<EmailBodyDisplayState | null>(null);
  const [bodyLoading, setBodyLoading] = useState(false);
  const loadBody = useCallback(
    async (force = false) => {
      if (!bodyClient || bodyLoading) return;
      if (body !== null && !force) return;
      setBodyLoading(true);
      try {
        // `published_from ?? signal_id`: the projection INSERT omits
        // `email_message_id`, so an owner tapping the shared projection has no
        // Message-ID to locate with. Sending the original resolves it.
        const id = signal.published_from ?? signal.signal_id;
        setBody(await fetchEmailBody(bodyClient, id));
      } catch (err) {
        // The route answers 200 with a reason for every EXPECTED failure, so
        // reaching here is a transport or status failure — and those are not
        // all the same thing. Mapping every one to `mailbox_unreachable` put a
        // "Mailbox unreachable — try again shortly" with a Try again button in
        // front of a 429 (where retrying immediately 429s again) and a 404
        // (where it can never succeed).
        setBody(bodyErrorState(err));
      } finally {
        setBodyLoading(false);
      }
    },
    [body, bodyClient, bodyLoading, signal.published_from, signal.signal_id],
  );
  // Reply is only offered when there's a sender to reply to and a host provided
  // the callback — the card never renders "Send this to undefined" downstream.
  // Every send affordance is gated on owning the mailbox. The inbox is a household
  // UNION, so a signal another member shared is visible here while living in their
  // account — Reply would open a composer that fails at send, and Reply all 404s
  // outright. Offering an action that cannot work is worse than not offering it.
  const canReply = sendable && Boolean(onReply) && Boolean((signal.from_addr ?? "").trim());
  const canForward = sendable && Boolean(onForward);
  // LUM-649: the Important dot uses the SAME additive formula as deriveLane, so a
  // card's dot never disagrees with the lane the learned nudge placed it in.
  const important =
    (signal.importance_score ?? 0) + (signal.learned_relevance_delta ?? 0) >= NEEDS_YOU_IMPORTANCE;
  const time = timeLabel(signal);
  const triaged = signal.triage_status ?? null;
  const isDone = triaged === "done";
  const isSnoozed = triaged === "snoozed";

  return (
    <li className="lumogis-email-card" data-testid={`email-card-${signal.signal_id}`}>
      <div className="lumogis-email-card__head">
        {/* LUM-696: the checkbox is also the KEYBOARD CURSOR anchor — moving the
            cursor calls .focus() on it, so the browser's native :focus-visible
            ring is the visible focus state, screen readers announce the row, and
            the visual cursor cannot drift from real focus. No
            aria-activedescendant bookkeeping. */}
        {onToggleSelect ? (
          <input
            type="checkbox"
            className="lumogis-email-card__select"
            data-triage-checkbox={signal.signal_id}
            aria-label={`Select: ${signal.title} from ${senderLabel(signal)}`}
            checked={selected}
            onChange={(e) =>
              onToggleSelect(
                signal.signal_id,
                // jsdom and browsers both set shiftKey on the synthesized click;
                // a keyboard Space yields false, which is the correct plain
                // toggle.
                Boolean((e.nativeEvent as MouseEvent).shiftKey),
              )
            }
          />
        ) : null}
        {important ? (
          <span className="lumogis-email-card__important" aria-label="Important" title="Important">
            ●
          </span>
        ) : null}
        <span className="lumogis-email-card__sender">{senderLabel(signal)}</span>
        {/* LUM-821: which mailbox this arrived in, on a multi-mailbox account.
            On screen the chip sits beside the sender and reads as what it is.
            Announced bare it would be one more proper noun in a row of them, so
            the prefix is carried by REAL text that is visually hidden — NOT by
            `aria-label`, which a browser drops here: a `<span>` with no role is
            `role=generic`, which prohibits naming, so the label would never be
            announced while looking correct in the source and in any test that
            queries the attribute rather than the computed name.
            `title` stays for the mouse, and carries the untruncated name. */}
        {mailboxLabel ? (
          <span className="lumogis-email-card__mailbox" title={`Mailbox: ${mailboxLabel}`}>
            <span className="lumogis-visually-hidden">Mailbox: </span>
            {mailboxLabel}
          </span>
        ) : null}
        {time ? <time className="lumogis-email-card__time">{time}</time> : null}
      </div>
      <div className="lumogis-email-card__title">{signal.title}</div>
      {signal.content_summary ? (
        <p className="lumogis-email-card__summary">{signal.content_summary}</p>
      ) : null}
      {signal.category_label ? (
        <span className="lumogis-email-card__chip" data-category-slug={signal.category}>
          {signal.category_label}
        </span>
      ) : null}

      <div className="lumogis-email-card__actions" role="group" aria-label="Triage actions">
        {isDone || isSnoozed ? (
          <button
            type="button"
            className="lumogis-email-card__action"
            disabled={pending}
            onClick={() => onClearTriage(signal.signal_id)}
          >
            {isSnoozed ? "Un-snooze" : "Reopen"}
          </button>
        ) : (
          <>
            {canReply ? (
              <button
                type="button"
                className="lumogis-email-card__action"
                disabled={pending}
                onClick={() => onReply?.(signal)}
              >
                Reply
              </button>
            ) : null}
            {canReply && onReplyAll ? (
              <button
                type="button"
                className="lumogis-email-card__action"
                disabled={pending}
                onClick={() => onReplyAll(signal)}
              >
                Reply all
              </button>
            ) : null}
            {canForward ? (
              <button
                type="button"
                className="lumogis-email-card__action"
                disabled={pending}
                onClick={() => onForward?.(signal)}
              >
                Forward
              </button>
            ) : null}
            <button
              type="button"
              className="lumogis-email-card__action"
              disabled={pending}
              onClick={() => onSetTriage(signal.signal_id, "done")}
            >
              Done
            </button>
            <details className="lumogis-email-card__snooze">
              <summary>Snooze</summary>
              <ul className="lumogis-email-card__snooze-menu">
                {SNOOZE_PRESETS.map((preset) => (
                  <li key={preset.label}>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() =>
                        onSetTriage(signal.signal_id, "snoozed", preset.at().toISOString())
                      }
                    >
                      {preset.label}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
            <button
              type="button"
              className="lumogis-email-card__action"
              aria-label="Flag more like this"
              title="Flag more like this"
              disabled={pending}
              onClick={() => onFeedback(signal.signal_id, true)}
            >
              👍
            </button>
            <button
              type="button"
              className="lumogis-email-card__action"
              disabled={pending}
              onClick={() => {
                // LUM-649: "Not important" both reclassifies the lane AND teaches
                // the relevance profile (👎) — both fire on the same click.
                onSetTriage(signal.signal_id, "good_to_know");
                onFeedback(signal.signal_id, false);
              }}
            >
              Not important
            </button>
          </>
        )}
      </div>

      {/* LUM-695: lazy by design — the fetch fires on first open, not on render,
          so an inbox of 200 cards is 200 requests only if the user opens 200. */}
      {bodyClient ? (
        readable ? (
          <details
            className="lumogis-email-card__body"
            data-testid={`body-${signal.signal_id}`}
            onToggle={(e) => {
              if ((e.currentTarget as HTMLDetailsElement).open) void loadBody();
            }}
          >
            <summary>Message</summary>
            <EmailBody
              body={body?.body ?? ""}
              // LUM-860: "not fetched yet" is loading too. Opening the
              // disclosure shows its content in the same frame, before the
              // toggle event has started the fetch; without `body === null`
              // that frame is an empty body under a "Showing text only" footer.
              loading={bodyLoading || body === null}
              truncated={body?.truncated ?? false}
              degraded={body?.degraded ?? false}
              reason={body?.reason ?? "ok"}
              inlineImages={body?.inline_images ?? []}
              inlineImagesTruncated={body?.inline_images_truncated ?? false}
              onRetry={() => void loadBody(true)}
            />
          </details>
        ) : (
          // Decided client-side from the ownership predicate, never by probing
          // the route and reading a 404 — a probing implementation invites
          // someone to "fix" that 404 later.
          <p className="lumogis-email-card__body-unavailable" role="note">
            Shared by {senderLabel(signal)} · only they can open this message
          </p>
        )
      ) : null}
      {attachmentClient ? (
        <details
          className="lumogis-email-card__attachments"
          data-testid={`attachments-${signal.signal_id}`}
          onToggle={(e) => {
            if ((e.currentTarget as HTMLDetailsElement).open) void loadAttachments();
          }}
        >
          <summary>Attachments</summary>
          <AttachmentList
            signalId={signal.signal_id}
            rows={attachments ?? []}
            available={attachmentsAvailable}
            loading={attachmentsLoading}
            error={attachmentsError}
            client={attachmentClient}
          />
        </details>
      ) : null}
    </li>
  );
}
