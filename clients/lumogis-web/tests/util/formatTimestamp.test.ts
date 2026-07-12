// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { formatAuditTimestamp } from "../../src/util/formatTimestamp";

describe("formatAuditTimestamp", () => {
  it("formats ISO to en-GB local string", () => {
    const out = formatAuditTimestamp("2026-07-09T17:47:08.988514Z");
    expect(out).toMatch(/9 Jul 2026/);
    expect(out).toMatch(/,\s*\d{2}:\d{2}$/);
  });

  it("returns em dash for empty", () => {
    expect(formatAuditTimestamp(null)).toBe("—");
  });
});
