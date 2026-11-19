// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/** Up to two letters from the part of the email before the @ ("anna.meier@…" → "AM"). */
export function initialsFor(email: string | undefined): string {
  const local = (email ?? "").split("@")[0] ?? "";
  const parts = local.split(/[._-]+/).filter(Boolean);
  const letters = parts.length >= 2 ? `${parts[0]![0]}${parts[1]![0]}` : local.slice(0, 1);
  return letters.toUpperCase() || "?";
}
