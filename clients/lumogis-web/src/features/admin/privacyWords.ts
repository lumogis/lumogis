// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/** The policy in words a household member reads (never the raw `local_only` / `allow_cloud`). */
export function privacyWords(policy: string): string {
  return policy === "allow_cloud" ? "Online AI allowed" : "Local AI only";
}
