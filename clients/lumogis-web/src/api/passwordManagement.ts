// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import type { ApiClient } from "./client";

/** Minimum password length — matches orchestrator login / bootstrap policy (12). */
export const MIN_PASSWORD_LENGTH = 12;

export async function changeMyPassword(
  client: ApiClient,
  opts: { currentPassword: string; newPassword: string },
): Promise<void> {
  await client.postJson<
    { current_password: string; new_password: string },
    { ok: boolean }
  >("/api/v1/me/password", {
    current_password: opts.currentPassword,
    new_password: opts.newPassword,
  });
}

export async function adminSetUserPassword(
  client: ApiClient,
  userId: string,
  opts: { newPassword: string },
): Promise<void> {
  await client.postJson<{ new_password: string }, { ok: boolean }>(
    `/api/v1/admin/users/${encodeURIComponent(userId)}/password`,
    { new_password: opts.newPassword },
  );
}
