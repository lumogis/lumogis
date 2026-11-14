// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { describe, expect, it } from "vitest";

import { clearInviteOnboardingHint, INVITE_ONBOARDING_STORAGE_KEY } from "../../src/api/invites";

describe("clearInviteOnboardingHint (LUM-886)", () => {
  it("removes only the onboarding hint", () => {
    sessionStorage.setItem(INVITE_ONBOARDING_STORAGE_KEY, "{}");
    sessionStorage.setItem("lumogis_login_flash", "keep");
    clearInviteOnboardingHint();
    expect(sessionStorage.getItem(INVITE_ONBOARDING_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem("lumogis_login_flash")).toBe("keep");
  });
});
