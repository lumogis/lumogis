// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-724 — where a document came from, and what you can do with it there.
//
// `/documents` is one library over three stores: files you uploaded, documents
// paperless holds, and attachments you filed from email. The list answers *what
// do I have*; the origin answers *where do I go to work with it*. That split is
// what makes one list over several stores honest rather than a leaky
// abstraction — a paperless row's primary action is "Open in paperless", not a
// Lumogis viewer, because paperless owns the artifact.
//
// **Derived from the path scheme, never from a stored column.** The logical path
// is already the stable key that the Qdrant payload, the KG evidence rows and
// purge all use, so a second field could disagree with it. Mirrors
// `services/external_document_index.origin_of`.

export type DocumentOrigin = "upload" | "paperless" | "email";

const PAPERLESS_SCHEME = "paperless://";
const EMAIL_ATTACHMENT_SCHEME = "email-attachment://";

/** A **prefix** test, matching the server. An uploaded file whose name happens
 *  to contain `://` must not be treated as external. */
export function originOf(filePath: string | null | undefined): DocumentOrigin {
  const path = filePath ?? "";
  if (path.startsWith(PAPERLESS_SCHEME)) return "paperless";
  if (path.startsWith(EMAIL_ATTACHMENT_SCHEME)) return "email";
  return "upload";
}

export function isExternal(filePath: string | null | undefined): boolean {
  return originOf(filePath) !== "upload";
}

/** Short label for the row badge. `upload` gets none — it is the unremarkable
 *  case, and badging every row would make the badge worthless. */
export const ORIGIN_LABELS: Record<DocumentOrigin, string> = {
  upload: "",
  paperless: "From paperless",
  email: "From email",
};

export function originLabel(filePath: string | null | undefined): string {
  return ORIGIN_LABELS[originOf(filePath)];
}

/** Why an external document cannot be shared with the household.
 *
 *  Said in the UI rather than discovered as a 409: the reason is not obvious,
 *  and a control that fails on click is worse than one that explains first. */
export const EXTERNAL_SHARE_EXPLANATION =
  "Documents from paperless or email can't be shared yet — the system they came " +
  "from can remove them, and a shared copy would outlive that.";

/** Re-ingest reads the file from disk, which a logical path does not name. */
export const EXTERNAL_REINGEST_EXPLANATION: Record<
  Exclude<DocumentOrigin, "upload">,
  string
> = {
  paperless: "This document lives in paperless — re-process it there.",
  email: "This came from an email attachment. Undo it and file it again to refresh.",
};

/** The paperless document id, for a deep link. `null` for anything else.
 *
 *  The base URL is **not** derived here: it is server-side configuration, and
 *  building it from anything in an API response would let a compromised
 *  upstream choose where the user is sent. */
export function paperlessDocumentId(filePath: string | null | undefined): string | null {
  const path = filePath ?? "";
  if (!path.startsWith(PAPERLESS_SCHEME)) return null;
  const id = path.split("/").pop() ?? "";
  return /^\d+$/.test(id) ? id : null;
}
