// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Display helpers for the admin users panel. Extracted from AdminUsersView so the
// component module exports only its component (react-refresh/only-export-components).

/** Display label for a wire role value. The wire value stays `user`; the UI calls it
 * "Member". Any other (future) role is passed through verbatim rather than mislabelled. */
export function roleLabel(role: string): string {
  if (role === "admin") return "Admin";
  if (role === "user") return "Member";
  return role;
}

/** Human-friendly "last active" cell from `last_seen_at` (null/undefined = never seen). */
export function formatLastActive(value: string | null | undefined): string {
  if (!value) return "never";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
