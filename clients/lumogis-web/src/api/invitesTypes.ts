// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { LoginResponse } from "./auth";

export interface InvitePeekPublic {
  allows_shared: boolean;
  expires_at: string;
}

export interface InviteOnboardingHint {
  allows_shared: boolean;
}

export interface InviteRedeemResponse extends LoginResponse {
  invite_onboarding: InviteOnboardingHint;
}

export interface InviteAdminRow {
  id: string;
  role: string;
  allows_shared: boolean;
  created_by: string;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  used_by: string | null;
  revoked_at: string | null;
  token_prefix: string | null;
}

// Request body for POST /api/v1/admin/users/invites. ``allows_shared`` is the
// per-user shared-scope gate (LUM-577) stamped onto the invite and copied to
// ``users.allows_shared`` at redemption; the server defaults it to true.
export interface InviteMintRequest {
  role: "admin" | "user";
  allows_shared: boolean;
}

export interface InviteMintResponse {
  invite: InviteAdminRow;
  invite_url: string;
  token: string;
}

export const INVITE_ONBOARDING_STORAGE_KEY = "lumogis_invite_onboarding";

export interface InviteOnboardingHintStored {
  allows_shared: boolean;
}
