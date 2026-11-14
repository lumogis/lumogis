// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { describe, expect, it } from "vitest";

import { formatCategoryLabel, shouldShowCategoryChip } from "../../../src/features/signals/categoryLabels";

describe("categoryLabels", () => {
  it("formatCategoryLabel trims display text", () => {
    expect(formatCategoryLabel("  School  ")).toBe("School");
  });

  it("shouldShowCategoryChip is false for empty labels", () => {
    expect(shouldShowCategoryChip("")).toBe(false);
    expect(shouldShowCategoryChip("   ")).toBe(false);
    expect(shouldShowCategoryChip(null)).toBe(false);
  });

  it("shouldShowCategoryChip is true for resolved taxonomy labels", () => {
    expect(shouldShowCategoryChip("Landlord")).toBe(true);
    expect(shouldShowCategoryChip("a_landlord")).toBe(true);
  });
});
