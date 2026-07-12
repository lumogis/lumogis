// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/** Truncate long paths/IDs for one-line captions; full value available elsewhere. */
export function middleEllipsis(text: string, maxLen = 52): string {
  if (text.length <= maxLen) return text;
  const budget = maxLen - 1;
  const head = Math.ceil(budget * 0.35);
  const tail = Math.floor(budget * 0.65);
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}
