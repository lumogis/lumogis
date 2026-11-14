// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Test helper (LUM-886): mark this jsdom "device" and tab as already reconciled for
 * `userId`, so a suite that pre-seeds drafts, threads or outbox rows before mounting a
 * real `AuthProvider` does not have them wiped by the first-sign-in reconcile.
 */
import { LAST_SIGNED_IN_USER_KEY, TAB_OWNER_KEY } from "../../src/auth/clientStateBoundary";

export function seedSignedInDevice(userId: string): void {
  localStorage.setItem(LAST_SIGNED_IN_USER_KEY, userId);
  sessionStorage.setItem(TAB_OWNER_KEY, userId);
}
