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

/** Layout lives here rather than in a stylesheet, matching `EmailBody`'s
 *  `pre-wrap`: this feature's classNames are test/theme hooks, and nothing
 *  ships CSS for them. The bounding is not cosmetic — a sender picks the pixel
 *  dimensions, and a 1200px banner is ordinary marketing mail. */
const LIST_STYLE = {
  display: "flex",
  flexWrap: "wrap",
  gap: "0.5rem",
  listStyle: "none",
  margin: "0.5rem 0 0",
  padding: 0,
} as const;

const IMAGE_STYLE = {
  // The pair that contains a sender-sized image inside a phone-width card.
  maxWidth: "100%",
  // Capped so a tall scan cannot push the rest of the card off-screen; the
  // reader opens it in their mail app for the full thing, as the rest of this
  // surface already tells them.
  maxHeight: "20rem",
  width: "auto",
  height: "auto",
  objectFit: "contain",
} as const;

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
    <div className="lumogis-inline-strip" data-testid="inline-image-strip">
      <ul className="lumogis-inline-strip__list" style={LIST_STYLE}>
        {images.map((image, index) => (
          // `content_id` is sender-controlled and need not be unique, so the
          // index goes in the key too. The list is never reordered or filtered
          // after render, which is what makes an index safe here.
          <li className="lumogis-inline-strip__item" key={`${image.content_id}-${index}`}>
            <img
              className="lumogis-inline-strip__image"
              style={IMAGE_STYLE}
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
        <p className="lumogis-inline-strip__truncated" role="note">
          Some images weren’t shown — open the message in your mail app to see them all.
        </p>
      ) : null}
    </div>
  );
}

export default InlineImageStrip;
