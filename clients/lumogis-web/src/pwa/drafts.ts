// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// IndexedDB draft text only (Phase 3C). Keys are thread/capture identifiers — never
// user email, JWT, or structured messages. Fail-soft when IDB unavailable.
//

import { del, delMany, get, keys, set } from "idb-keyval";

/** Conservative cap (~32 KiB Unicode code units); avoids bloating IndexedDB when pasting dumps. */
export const DRAFT_MAX_CHARS = 32_768;

const DB_PREFIX_CHAT = "lumogis:draft:chat:";
const DB_PREFIX_CAPTURE = "lumogis:draft:capture:";
/** Shared prefix of every draft key — used to remove all drafts at sign-out. */
const DB_PREFIX_ALL = "lumogis:draft:";

export function makeChatDraftKey(threadId: string): string {
  return `${DB_PREFIX_CHAT}${threadId}`;
}

/** Reserved for Phase 5 capture UI — wired in tests/docs only for now. */
export function makeCaptureDraftKey(captureId: string): string {
  return `${DB_PREFIX_CAPTURE}${captureId}`;
}

function truncate(text: string): string {
  if (text.length <= DRAFT_MAX_CHARS) return text;
  return text.slice(0, DRAFT_MAX_CHARS);
}

export async function getDraft(key: string): Promise<string | undefined> {
  try {
    const v = await get(key);
    if (typeof v !== "string") return undefined;
    return truncate(v);
  } catch {
    return undefined;
  }
}

/** Persists text only; trims length to cap; deletes when empty or whitespace-only. */
export async function setDraft(key: string, raw: string): Promise<void> {
  if (raw.trim().length === 0) {
    await deleteDraftQuiet(key);
    return;
  }
  try {
    await set(key, truncate(raw));
  } catch {
    /* IDB unavailable or quota — keep in-memory textarea only */
  }
}

export async function deleteDraft(key: string): Promise<void> {
  await deleteDraftQuiet(key);
}

async function deleteDraftQuiet(key: string): Promise<void> {
  try {
    await del(key);
  } catch {
    /* soft fail */
  }
}

/**
 * Delete every draft (chat and capture) by key prefix — called by the sign-out
 * boundary module (LUM-886). Returns `false` when IndexedDB failed.
 *
 * Never use the store-wide `clear()` here: the capture outbox shares this default
 * store and holds the only copy of unsynced captures. `keys()` and `delMany` are two
 * transactions, so a draft written between them by another live tab survives; that
 * tab is signed out by the boundary module and the next sign-in wipe is the backstop.
 */
export async function clearAllDrafts(): Promise<boolean> {
  try {
    const all = await keys();
    const mine = all.filter((k) => typeof k === "string" && k.startsWith(DB_PREFIX_ALL));
    if (mine.length > 0) await delMany(mine);
    return true;
  } catch {
    return false;
  }
}
