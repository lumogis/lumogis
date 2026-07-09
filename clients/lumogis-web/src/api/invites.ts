// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

export type {
  InviteAdminRow,
  InviteMintRequest,
  InviteMintResponse,
  InviteOnboardingHint,
  InviteOnboardingHintStored,
  InvitePeekPublic,
  InviteRedeemResponse,
} from "./invitesTypes";
export { INVITE_ONBOARDING_STORAGE_KEY } from "./invitesTypes";

import type { ApiClient } from "./client";
import type {
  InviteAdminRow,
  InviteMintRequest,
  InviteMintResponse,
  InvitePeekPublic,
  InviteRedeemResponse,
} from "./invitesTypes";

export async function peekInvite(token: string): Promise<InvitePeekPublic> {
  const res = await fetch(`/api/v1/invites/${encodeURIComponent(token)}`);
  if (!res.ok) {
    let detail = "Invite link is invalid or expired";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await res.json()) as InvitePeekPublic;
}

export async function redeemInvite(
  token: string,
  email: string,
  password: string,
): Promise<InviteRedeemResponse> {
  const res = await fetch(`/api/v1/invites/${encodeURIComponent(token)}/redeem`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    let detail = "Redemption failed";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await res.json()) as InviteRedeemResponse;
}

export async function mintAdminInvite(
  client: ApiClient,
  body: { role: "admin" | "user"; allows_shared?: boolean },
): Promise<InviteMintResponse> {
  // ``allows_shared`` defaults to true (server default) until the invite UI
  // exposes the per-user shared-scope choice (LUM-577 follow-up).
  const payload: InviteMintRequest = {
    role: body.role,
    allows_shared: body.allows_shared ?? true,
  };
  return client.postJson<InviteMintRequest, InviteMintResponse>(
    "/api/v1/admin/users/invites",
    payload,
  );
}

export async function listAdminInvites(client: ApiClient): Promise<InviteAdminRow[]> {
  const res = await client.getJson<{ invites: InviteAdminRow[] }>("/api/v1/admin/users/invites");
  return res.invites;
}

export async function revokeAdminInvite(client: ApiClient, inviteId: string): Promise<void> {
  await client.delete(`/api/v1/admin/users/invites/${encodeURIComponent(inviteId)}`);
}
