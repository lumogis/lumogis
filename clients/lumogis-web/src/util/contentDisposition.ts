// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/** Parse RFC 5987 / simple `filename="..."` from Content-Disposition. */
export function parseFilenameFromContentDisposition(header: string | null): string | null {
  if (header == null || header.length === 0) return null;
  const mStar = /filename\*=(?:UTF-8''|)([^;\n]+)/i.exec(header);
  if (mStar?.[1]) {
    try {
      return decodeURIComponent(mStar[1].trim().replace(/^"|"$/g, ""));
    } catch {
      return mStar[1].trim();
    }
  }
  const m = /filename="([^"]*)"/i.exec(header) ?? /filename=([^;\s]+)/i.exec(header);
  if (m?.[1]) return m[1].trim();
  return null;
}
