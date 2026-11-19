// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-810 — reading a message.
//
// Before this, the only way to read your own mail was to press Reply and
// expand the quote disclosure. The card showed an LLM summary and asked you to
// triage on it.
//
// Two decisions from the ADR are visible here:
//
// 1. **Text, never rendered HTML.** No sender-controlled bytes enter a rendered
//    HTML context, so remote tracking pixels and sanitiser-bypass XSS are
//    avoided rather than defended against. The cost is real — newsletters read
//    as flattened text — and the footer states it rather than leaving the
//    reader to discover it.
//
// 2. **Failures are explained, not hidden.** Every reason the server can give
//    has its own sentence. "Mailbox unreachable" is retryable and says so;
//    "no longer in your mailbox" is not, and says that instead.
//
// Presentational only: no fetching, no auth. Same testability posture as
// EmailComposer.

import { useState } from "react";

import { LoadingPlaceholder, SkeletonText } from "@/components/page/LoadingState";
import { Button } from "@/components/ui/button";

import type { JSX } from "react";

import type { EmailBodyDisplayReason, EmailInlineImage } from "../../api/emailSend";
import { InlineImageStrip } from "./InlineImageStrip";

/** Matches `RecoverableSends.tsx` — one truncate-and-expand behaviour across
 *  the surfaces that show message text. */
const BODY_PREVIEW_CHARS = 240;

export interface EmailBodyProps {
  body: string;
  loading?: boolean;
  truncated?: boolean;
  degraded?: boolean;
  reason?: EmailBodyDisplayReason;
  /** Images the sender embedded in the message (LUM-713). Already identified
   *  and bounded by the server; this component only places them. */
  inlineImages?: EmailInlineImage[];
  inlineImagesTruncated?: boolean;
  onRetry?: () => void;
}

/** The sentence for each failure. Written for someone who does not know what
 *  IMAP is and cannot act on a status code. */
function reasonCopy(reason: EmailBodyDisplayReason): string | null {
  switch (reason) {
    case "mailbox_unreachable":
      return "Mailbox unreachable — try again shortly.";
    case "rate_limited":
      // Retrying is exactly what trips this again — say wait, not try again.
      return "Too many messages opened just now — wait a minute and try again.";
    case "unavailable":
      // The 404 the ownership gate returns. Same copy as a genuine miss, so
      // the gate stays free of an oracle on the client side too.
      return "Can’t open this message.";
    case "no_body":
      return "No message text available.";
    case "not_found":
      return "This message is no longer in your mailbox.";
    case "too_large":
      return "Message is too large to show here — open it in your mail app.";
    case "ambiguous":
      return "Couldn’t identify this message uniquely.";
    default:
      return null;
  }
}

/** The footer must agree with what is actually on screen.
 *
 * Branching on `images.length > 0` alone contradicted the strip in the case
 * where a cap dropped EVERY image: the strip said "some images weren’t shown"
 * directly above a footer claiming images are not displayed at all. */
function footerCopy(imageCount: number, imagesTruncated: boolean): string {
  if (imageCount > 0) {
    return "Showing text and the message’s own images — formatting is not displayed.";
  }
  if (imagesTruncated) {
    // The strip is on screen and already explains the images. Repeating
    // "images are not displayed" here would read as a different reason.
    return "Showing text only — formatting is not displayed.";
  }
  return "Showing text only — images and formatting are not displayed.";
}

export function EmailBody({
  body,
  loading = false,
  truncated = false,
  degraded = false,
  reason = "ok",
  inlineImages = [],
  inlineImagesTruncated = false,
  onRetry,
}: EmailBodyProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  if (loading) {
    return (
      <LoadingPlaceholder label="Loading message…">
        <SkeletonText lines={3} />
      </LoadingPlaceholder>
    );
  }

  const copy = reasonCopy(reason);
  if (copy !== null) {
    return (
      <div className="flex flex-col items-start gap-2" role="note">
        <p className="m-0 text-sm text-muted-foreground">{copy}</p>
        {degraded && onRetry ? (
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
        {/* A message can have images and NO text — a scanned letter, a
            cid:-only marketing mail — and the server answers `no_body` for it.
            Returning here without the strip made the feature do nothing on
            precisely the messages it exists for. The other reasons carry no
            images, so this renders nothing for them. */}
        <InlineImageStrip images={inlineImages} truncated={inlineImagesTruncated} />
      </div>
    );
  }

  const longBody = body.length > BODY_PREVIEW_CHARS;
  const shown = expanded || !longBody ? body : `${body.slice(0, BODY_PREVIEW_CHARS)}…`;

  return (
    <div className="flex flex-col items-start gap-2">
      {/* `pre-wrap`: the extractor's newlines and table tabs ARE the layout
          once the HTML is gone. Collapsing them would undo the extraction. */}
      <p className="m-0 w-full text-sm break-words whitespace-pre-wrap">
        {shown}
      </p>
      {longBody ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? "Show less" : "Show all"}
        </Button>
      ) : null}
      {truncated ? (
        <p className="m-0 text-xs text-muted-foreground" role="note">
          Message truncated — open it in your mail app to read the rest.
        </p>
      ) : null}
      <InlineImageStrip images={inlineImages} truncated={inlineImagesTruncated} />
      {/* The footer states the limitation rather than leaving the reader to
          discover it — so it must stop claiming images are not displayed the
          moment some are, and must not contradict a strip that is on screen
          saying images were dropped. Formatting is never displayed. */}
      <p className="m-0 text-xs text-muted-foreground" role="note">
        {footerCopy(inlineImages.length, inlineImagesTruncated)}
      </p>
    </div>
  );
}

export default EmailBody;
