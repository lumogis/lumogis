// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { middleEllipsis } from "../../src/util/middleEllipsis";

describe("middleEllipsis", () => {
  it("shortens long paths on one line", () => {
    const path =
      "/workspace/uploads/1dde411648fc4eeebb7a53c49a5a2ff6/98cc7107cb1049b98ec422746f665368_household-insurance.md";
    const out = middleEllipsis(path, 52);
    expect(out).toContain("…");
    expect(out).toMatch(/insurance\.md$/);
    expect(out.length).toBeLessThanOrEqual(52);
  });

  it("leaves short strings unchanged", () => {
    expect(middleEllipsis("notes.txt")).toBe("notes.txt");
  });
});
