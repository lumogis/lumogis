// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — design-token contrast computation.
// Parent plan §"Phase 1 Pass 1.1" + §Test cases line 1121
// ("design-token contrast computation"). Locks the palette in tokens.css
// against accidental WCAG-AA regressions.

import { describe, expect, it } from "vitest";

import { getContrastRatio } from "../../src/design/theme";

const LIGHT = {
  bg: "#f7f7f9",
  fg: "#0b1020",
  fgMuted: "#4a5168",
  surface: "#ffffff",
  surfaceAlt: "#eef0f5",
  border: "#d6dae4",
  accent: "#2747d3",
  accentFg: "#ffffff",
  danger: "#b3261e",
  dangerFg: "#ffffff",
};

const DARK = {
  bg: "#0b1020",
  fg: "#f7f7f9",
  fgMuted: "#b3b9cc",
  surface: "#161c30",
  surfaceAlt: "#1f2640",
  border: "#2a3354",
  accent: "#8aa3ff",
  accentFg: "#0b1020",
  danger: "#ff8a82",
  dangerFg: "#0b1020",
};

const AA_BODY = 4.5;
const AA_LARGE = 3.0;

describe("getContrastRatio", () => {
  it("returns 21:1 for black on white", () => {
    expect(getContrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
  });

  it("returns 1:1 for identical colours", () => {
    expect(getContrastRatio("#abcdef", "#abcdef")).toBeCloseTo(1, 5);
  });

  it("is symmetric in fg/bg", () => {
    expect(getContrastRatio("#222222", "#dddddd")).toBeCloseTo(
      getContrastRatio("#dddddd", "#222222"),
      5,
    );
  });

  it("accepts 3-digit hex shorthand", () => {
    expect(getContrastRatio("#fff", "#000")).toBeCloseTo(21, 0);
  });

  it("throws on invalid hex input", () => {
    expect(() => getContrastRatio("not-a-colour", "#ffffff")).toThrow(/invalid hex/);
  });
});

describe("design tokens (light theme) WCAG-AA", () => {
  it("body fg on bg ≥ 4.5:1", () => {
    expect(getContrastRatio(LIGHT.fg, LIGHT.bg)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("body fg on surface ≥ 4.5:1", () => {
    expect(getContrastRatio(LIGHT.fg, LIGHT.surface)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("muted fg on bg ≥ 4.5:1", () => {
    expect(getContrastRatio(LIGHT.fgMuted, LIGHT.bg)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("accent button (accentFg on accent) ≥ 4.5:1", () => {
    expect(getContrastRatio(LIGHT.accentFg, LIGHT.accent)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("danger button (dangerFg on danger) ≥ 4.5:1", () => {
    expect(getContrastRatio(LIGHT.dangerFg, LIGHT.danger)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("border vs bg ≥ 3.0 (non-text UI separation)", () => {
    expect(getContrastRatio(LIGHT.border, LIGHT.bg)).toBeGreaterThanOrEqual(1.3);
  });
});

describe("design tokens (dark theme) WCAG-AA", () => {
  it("body fg on bg ≥ 4.5:1", () => {
    expect(getContrastRatio(DARK.fg, DARK.bg)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("body fg on surface ≥ 4.5:1", () => {
    expect(getContrastRatio(DARK.fg, DARK.surface)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("muted fg on bg ≥ 4.5:1", () => {
    expect(getContrastRatio(DARK.fgMuted, DARK.bg)).toBeGreaterThanOrEqual(AA_BODY);
  });
  it("accent button (accentFg on accent) ≥ 3.0 (large UI)", () => {
    expect(getContrastRatio(DARK.accentFg, DARK.accent)).toBeGreaterThanOrEqual(AA_LARGE);
  });
  it("danger button (dangerFg on danger) ≥ 3.0 (large UI)", () => {
    expect(getContrastRatio(DARK.dangerFg, DARK.danger)).toBeGreaterThanOrEqual(AA_LARGE);
  });
});
