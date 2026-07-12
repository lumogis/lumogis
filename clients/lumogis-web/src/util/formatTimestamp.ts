// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/** Friendly local timestamp for table cells (full ISO kept in detail payloads). */
export function formatAuditTimestamp(iso: string | null | undefined): string {
  if (iso == null || iso.trim() === "") return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
