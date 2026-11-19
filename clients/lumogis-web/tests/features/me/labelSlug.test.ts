// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { describe, expect, it } from "vitest";

import { labelSlug } from "../../../src/features/me/labelSlug";

const SERVER_RULE = /^[a-z][a-z0-9_]{0,31}$/;

describe("labelSlug", () => {
  it("makes the server's kind of name from what a person types", () => {
    expect(labelSlug("Receipts", [])).toBe("receipts");
    expect(labelSlug("Kids' school & clubs", [])).toBe("kids_school_clubs");
    expect(labelSlug("Rechnungen für Müller", [])).toBe("rechnungen_fur_muller");
    expect(labelSlug("2026 taxes", [])).toBe("taxes");
  });

  it("never clashes with a label that exists", () => {
    expect(labelSlug("School", ["school"])).toBe("school_2");
    expect(labelSlug("School", ["school", "school_2"])).toBe("school_3");
  });

  it("falls back to a numbered name when nothing usable is left", () => {
    expect(labelSlug("学校", [])).toBe("label_2");
    expect(labelSlug("!!!", ["label_2"])).toBe("label_3");
  });

  it("always matches the server rule, even for long names", () => {
    for (const name of ["x".repeat(80), "A very long label name that goes on and on and on", "École", "123", ""]) {
      const s = labelSlug(name, ["a".repeat(32)]);
      expect(s, name).toMatch(SERVER_RULE);
    }
    const long = "y".repeat(40);
    expect(labelSlug(long, ["y".repeat(32)])).toMatch(SERVER_RULE);
    expect(labelSlug(long, ["y".repeat(32)])).toHaveLength(32);
  });
});
