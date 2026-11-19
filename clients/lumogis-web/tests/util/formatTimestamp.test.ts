// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { formatDateTime } from "../../src/util/formatTimestamp";

describe("formatDateTime (the old formatAuditTimestamp cases)", () => {
  it("formats ISO to en-GB local string", () => {
    const out = formatDateTime("2026-07-09T17:47:08.988514Z");
    expect(out).toMatch(/9 Jul 2026/);
    expect(out).toMatch(/,\s*\d{2}:\d{2}$/);
  });

  it("returns em dash for empty", () => {
    expect(formatDateTime(null)).toBe("—");
  });
});

describe("formatDateTime", () => {
  it("uses the house format, with and without the time", () => {
    const iso = "2026-11-17T11:08:31Z";
    expect(formatDateTime(iso, { timeZone: "UTC" })).toBe("17 Nov 2026, 11:08");
    expect(formatDateTime(iso, { timeZone: "UTC", withTime: false })).toBe("17 Nov 2026");
    expect(formatDateTime(iso, { timeZone: "Europe/Berlin" })).toBe("17 Nov 2026, 12:08");
  });

  it("returns invalid input as it came and a dash for nothing", () => {
    expect(formatDateTime("not a date")).toBe("not a date");
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("  ")).toBe("—");
  });

});
