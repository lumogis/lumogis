// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/** Join an old address's query into its new target; keys in `to` win (LUM-993). */
export function mergeQuery(to: string, oldSearch: string): string {
  const [path, toQuery = ""] = to.split("?") as [string, string | undefined];
  const merged = new URLSearchParams(oldSearch);
  for (const [k, v] of new URLSearchParams(toQuery)) merged.set(k, v);
  const q = merged.toString();
  return q ? `${path}?${q}` : path;
}
