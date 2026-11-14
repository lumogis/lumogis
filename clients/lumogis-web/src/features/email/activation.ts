// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-642/LUM-655: "email activated" signal for the nav gate. Email is a core
// opt-in integration — the destination appears once a mailbox is configured.
// Kept in its own module so a future explicit `email_enabled` toggle / marketplace
// (LUM-171) can swap the source without touching the view or the shell.

import type { SourceRow } from "../../api/emailSources";

export function isEmailActivated(sources: SourceRow[] | undefined): boolean {
  return (sources ?? []).some((s) => s.source_type === "imap");
}

// LUM-658: last-known activation, cached in localStorage so the Email nav item
// renders immediately on a cold load instead of popping in ~1 RTT after the
// sources query resolves (the app's react-query persistence allowlist is empty).
// Fail-closed: a read error or absent key returns false (item hidden).
const ACTIVATION_CACHE_KEY = "lumogis:email:activated";

export function readCachedEmailActivated(): boolean {
  try {
    return globalThis.localStorage?.getItem(ACTIVATION_CACHE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Remove the cached hint at sign-out (LUM-886): it describes the previous member. */
export function clearCachedEmailActivated(): void {
  try {
    globalThis.localStorage?.removeItem(ACTIVATION_CACHE_KEY);
  } catch {
    /* storage disabled — nothing to clear */
  }
}

export function writeCachedEmailActivated(active: boolean): void {
  try {
    globalThis.localStorage?.setItem(ACTIVATION_CACHE_KEY, active ? "1" : "0");
  } catch {
    /* private mode / storage disabled — non-fatal, just no cache */
  }
}
