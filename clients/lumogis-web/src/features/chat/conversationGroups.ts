// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Pure helpers for grouping server-backed conversations by ended_at (LUM-162).

export type ConversationGroup = "today" | "yesterday" | "last7" | "older";

export function groupConversationByEndedAt(
  endedAtIso: string,
  now = Date.now(),
): ConversationGroup {
  const ended = new Date(endedAtIso).getTime();
  if (Number.isNaN(ended)) return "older";
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfYesterday.getDate() - 1);
  const startOf7 = new Date(startOfToday);
  startOf7.setDate(startOf7.getDate() - 7);
  if (ended >= startOfToday.getTime()) return "today";
  if (ended >= startOfYesterday.getTime()) return "yesterday";
  if (ended >= startOf7.getTime()) return "last7";
  return "older";
}

export const CONVERSATION_GROUP_LABELS: Record<ConversationGroup, string> = {
  today: "Today",
  yesterday: "Yesterday",
  last7: "Last 7 days",
  older: "Older",
};

export const CONVERSATION_GROUP_ORDER: ConversationGroup[] = [
  "today",
  "yesterday",
  "last7",
  "older",
];
