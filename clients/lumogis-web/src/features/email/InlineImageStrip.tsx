// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-713 — the images a sender embedded *in* a message.
//
// A `cid:` inline image was visible nowhere in Lumogis: the attachment
// classifier excludes it by design (a signature logo is not a file you
// received), and ADR-207 renders no sender HTML for it to appear in. The
// photo, the scanned letter, the chart — all invisible. This strip is where
// they become visible, below the extracted text.
//
// **Nothing here decides what an image is.** The server sniffed the bytes,
// refused anything outside a four-format allowlist, and shipped what survived
// as a bounded `data:` URI. So this component fetches nothing, holds no object
// URL, and has no cleanup to get wrong — the entire lifecycle a blob-URL
// design would have needed simply does not exist.

import type { JSX } from "react";

import type { EmailInlineImage } from "../../api/emailSend";

export interface InlineImageStripProps {
  images: EmailInlineImage[];
  /** A cap dropped at least one image. Say so — a strip that silently shows
   *  four of six looks complete. */
  truncated?: boolean;
}

export function InlineImageStrip({
  images,
  truncated = false,
}: InlineImageStripProps): JSX.Element | null {
  if (images.length === 0 && !truncated) return null;

  return (
    <div data-testid="inline-image-strip">
      {/* The bounding on each image is not cosmetic: a sender picks the pixel
          dimensions, and a 1200px banner is ordinary marketing mail. */}
      <ul className="mx-0 mt-2 mb-0 flex list-none flex-wrap gap-2 p-0">
        {images.map((image, index) => (
          // `content_id` is sender-controlled and need not be unique, so the
          // index goes in the key too. The list is never reordered or filtered
          // after render, which is what makes an index safe here.
          <li key={`${image.content_id}-${index}`}>
            <img
              // max-w-full contains a sender-sized image inside a phone-width card.
              // Height is capped so a tall scan cannot push the rest of the card
              // off-screen; the reader opens it in their mail app for the full thing.
              className="h-auto max-h-80 w-auto max-w-full object-contain"
              src={image.data_uri}
              // The message text is the caption an inline image never carries;
              // its Content-ID is a machine handle, not a description. Number
              // them so a screen reader can at least say which is which.
              alt={`Image ${index + 1} of ${images.length} from this message`}
              loading="lazy"
            />
          </li>
        ))}
      </ul>
      {truncated ? (
        <p className="mt-2 text-sm text-muted-foreground" role="note">
          Some images weren’t shown — open the message in your mail app to see them all.
        </p>
      ) : null}
    </div>
  );
}

export default InlineImageStrip;
