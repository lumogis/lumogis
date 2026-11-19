// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

export interface FormatDateTimeOptions {
  /** Include the time of day (default true): "17 Nov 2026, 11:08" vs "17 Nov 2026". */
  withTime?: boolean;
  /** IANA zone; defaults to the browser's. Tests pin it. */
  timeZone?: string;
}

/**
 * The house date format (LUM-993): "17 Nov 2026, 11:08", or "17 Nov 2026"
 * without the time. Null or empty gives "—"; an unparseable value is returned
 * as it came, so nothing is silently hidden.
 */
export function formatDateTime(
  iso: string | null | undefined,
  { withTime = true, timeZone }: FormatDateTimeOptions = {},
): string {
  if (iso == null || iso.trim() === "") return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
    ...(timeZone ? { timeZone } : {}),
  });
}
