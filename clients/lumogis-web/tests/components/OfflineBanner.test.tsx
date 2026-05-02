// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  OfflineBanner,
  OFFLINE_BANNER_COPY,
} from "../../src/components/OfflineBanner";

describe("OfflineBanner (Phase 3E)", () => {
  it("is hidden when online", () => {
    render(<OfflineBanner visible={false} />);
    expect(screen.queryByTestId("lumogis-offline-banner")).toBeNull();
  });

  it("is visible offline with polite status and limited-support copy", () => {
    render(<OfflineBanner visible />);
    const banner = screen.getByTestId("lumogis-offline-banner");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveAttribute("aria-live", "polite");
    expect(banner.textContent ?? "").toBe(OFFLINE_BANNER_COPY);
    expect(OFFLINE_BANNER_COPY.toLowerCase()).toContain("offline");
    expect(OFFLINE_BANNER_COPY.toLowerCase()).toContain("draft");
    expect(OFFLINE_BANNER_COPY.toLowerCase()).not.toMatch(/offline.*full feature|works offline/i);
  });
});
