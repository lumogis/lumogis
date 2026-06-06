// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { describe, expect, it, vi } from "vitest";
import {
  canManageIngestPaths,
  canUploadIngest,
  isSearchDisabled,
  needsOnboarding,
  onboardingContinueEnabled,
} from "./overlayUi";

describe("isSearchDisabled", () => {
  it("searchEnabled_without_roots_when_authenticated", () => {
    expect(isSearchDisabled(false, "off")).toBe(false);
  });

  it("searchDisabled_when_needs_login", () => {
    expect(isSearchDisabled(true, "on")).toBe(true);
  });

  it("searchDisabled_when_unreachable", () => {
    expect(isSearchDisabled(false, "unreachable")).toBe(true);
  });
});

describe("canManageIngestPaths", () => {
  it("canManageIngestPaths_admin_only", () => {
    expect(canManageIngestPaths("off", null)).toBe(true);
    expect(canManageIngestPaths("on", "admin")).toBe(true);
    expect(canManageIngestPaths("on", "user")).toBe(false);
  });
});

describe("canUploadIngest", () => {
  it("allows upload for auth off and signed-in user", () => {
    expect(canUploadIngest("off", false)).toBe(true);
    expect(canUploadIngest("on", true)).toBe(true);
    expect(canUploadIngest("on", false)).toBe(false);
  });
});

describe("needsOnboarding", () => {
  it("needsOnboarding_false_when_complete", () => {
    expect(needsOnboarding({ onboardingComplete: true, libraryRoots: [] })).toBe(false);
  });

  it("needsOnboarding_true_when_incomplete", () => {
    expect(needsOnboarding({ onboardingComplete: false, libraryRoots: [] })).toBe(true);
  });
});

describe("onboardingContinueEnabled", () => {
  it("blocks until health ok and auth satisfied", () => {
    expect(onboardingContinueEnabled("ok", "off", false)).toBe(true);
    expect(onboardingContinueEnabled("ok", "on", true)).toBe(true);
    expect(onboardingContinueEnabled("ok", "on", false)).toBe(false);
    expect(onboardingContinueEnabled("degraded", "off", false)).toBe(false);
  });
});

describe("onboarding_test_connection_passes_typed_url", () => {
  it("probe_server_health receives wizard URL not localhost default", async () => {
    const invoke = vi.fn(async (_cmd: string, args: { orchestratorBaseUrl: string }) => {
      expect(args.orchestratorBaseUrl).toBe("https://household.example");
      return { status: "ok" };
    });
    await invoke("probe_server_health", {
      orchestratorBaseUrl: "https://household.example",
    });
    expect(invoke).toHaveBeenCalledWith("probe_server_health", {
      orchestratorBaseUrl: "https://household.example",
    });
  });
});
