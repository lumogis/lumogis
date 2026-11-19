// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// WCAG AA for the colour pairs the new components use (LUM-993). Unlike
// contrast.test.ts, which keeps hand-made copies of the palette, this test reads
// the real src/design/tokens.css and src/design/ui.css, so a palette edit that
// breaks contrast fails here. It covers all four theme states: system light,
// system dark (the prefers-color-scheme block), and the explicit data-theme
// light and dark overrides.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { getContrastRatio } from "../../src/design/theme";

const DESIGN = resolve(__dirname, "../../src/design");
const tokensCss = readFileSync(resolve(DESIGN, "tokens.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const uiCss = readFileSync(resolve(DESIGN, "ui.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** The body of the first `selector { … }` block after `from`, braces matched. */
function block(css: string, selector: string, from = 0): string {
  const start = css.indexOf(`${selector} {`, from);
  if (start < 0) throw new Error(`no block for ${selector}`);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return css.slice(css.indexOf("{", start) + 1, i);
  }
  throw new Error(`unclosed block for ${selector}`);
}

function vars(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const root = vars(block(tokensCss, ":root"));
const mediaDark = vars(block(block(tokensCss, "@media (prefers-color-scheme: dark)"), ":root"));
const explicitLight = vars(block(tokensCss, ':root[data-theme="light"]'));
const explicitDark = vars(block(tokensCss, ':root[data-theme="dark"]'));
const shadcn = vars(block(uiCss, ":root"));

const THEMES: Record<string, Record<string, string>> = {
  "system light": { ...root, ...shadcn },
  "system dark": { ...root, ...mediaDark, ...shadcn },
  "explicit light": { ...root, ...explicitLight, ...shadcn },
  "explicit dark": { ...root, ...explicitDark, ...shadcn },
};

function hex(n: number): string {
  return Math.round(n).toString(16).padStart(2, "0");
}

function rgb(h: string): [number, number, number] {
  const v = h.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as [number, number, number];
}

/** `a` at `pct` percent over `b`, as color-mix(in srgb) and `bg-x/NN` both do. */
function mix(a: string, b: string, pct: number): string {
  const [x, y] = [rgb(a), rgb(b)];
  return `#${x.map((c, i) => hex((c * pct + y[i] * (100 - pct)) / 100)).join("")}`;
}

/** Resolves a token to a #rrggbb colour: var() chains and color-mix(in srgb …). */
function color(theme: Record<string, string>, name: string, seen: string[] = []): string {
  if (seen.includes(name)) throw new Error(`cycle: ${[...seen, name].join(" -> ")}`);
  const raw = theme[name];
  if (raw === undefined) throw new Error(`${name} is not defined`);
  return value(theme, raw, [...seen, name]);
}

function value(theme: Record<string, string>, raw: string, seen: string[]): string {
  const v = raw.trim();
  if (/^#[0-9a-f]{6}$/i.test(v)) return v.toLowerCase();
  const ref = /^var\((--[\w-]+)\)$/.exec(v);
  if (ref) return color(theme, ref[1], seen);
  const cm = /^color-mix\(in srgb,\s*(.+?)\s+(\d+)%,\s*(.+)\)$/.exec(v);
  if (cm) return mix(value(theme, cm[1], seen), value(theme, cm[3], seen), Number(cm[2]));
  throw new Error(`cannot resolve "${v}"`);
}

const AA_TEXT = 4.5;
const AA_UI = 3; // WCAG 1.4.11: borders and focus rings

// [label, foreground, background, minimum]. A background written as
// "mix:<token>:<pct>:<over>" is a translucent tint, like ui/button's
// `bg-destructive/10` over the page.
const PAIRS: Array<[string, string, string, number]> = [
  ["body text on page", "--foreground", "--background", AA_TEXT],
  ["body text on card", "--card-foreground", "--card", AA_TEXT],
  ["body text on muted", "--foreground", "--muted", AA_TEXT],
  ["muted text on page", "--muted-foreground", "--background", AA_TEXT],
  ["muted text on card", "--muted-foreground", "--card", AA_TEXT],
  ["muted text on muted", "--muted-foreground", "--muted", AA_TEXT],
  ["primary button label", "--primary-foreground", "--primary", AA_TEXT],
  ["link and nav text on page", "--accent-text", "--background", AA_TEXT],
  ["link text on card", "--accent-text", "--card", AA_TEXT],
  ["active nav text on accent-soft", "--accent-text", "--lumogis-accent-soft", AA_TEXT],
  ["danger text on page", "--destructive", "--background", AA_TEXT],
  ["danger button label on its tint", "--destructive", "mix:--destructive:10:--background", AA_TEXT],
  ["dark danger button label on its tint", "--destructive", "mix:--destructive:20:--background", AA_TEXT],
  ["solid danger button label", "--destructive-foreground", "--destructive", AA_TEXT],
  ["success text on page", "--success", "--background", AA_TEXT],
  ["success text on card", "--success", "--card", AA_TEXT],
  ["warning text on page", "--warning", "--background", AA_TEXT],
  ["warning text on card", "--warning", "--card", AA_TEXT],
  ["text in a field", "--foreground", "--field", AA_TEXT],
  ["field border on card", "--input", "--card", AA_UI],
  ["field border on page", "--input", "--background", AA_UI],
  ["focus ring on page", "--ring", "--background", AA_UI],
];

function bg(theme: Record<string, string>, spec: string): string {
  if (!spec.startsWith("mix:")) return color(theme, spec);
  const [, tok, pct, over] = spec.split(":");
  return mix(color(theme, tok), color(theme, over), Number(pct));
}

describe("token contrast (real tokens.css and ui.css)", () => {
  for (const [themeName, theme] of Object.entries(THEMES)) {
    describe(themeName, () => {
      const pairs = themeName.endsWith("dark") ? PAIRS : PAIRS.filter(([label]) => !label.startsWith("dark "));
      it.each(pairs)("%s", (_label, fg, back, min) => {
        const ratio = getContrastRatio(color(theme, fg), bg(theme, back));
        expect(ratio, `${fg} on ${back}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(min);
      });
    });
  }

  it("resolves var() and color-mix() (the parser can fail)", () => {
    const t = { "--a": "#000000", "--b": "#ffffff", "--c": "var(--a)", "--d": "color-mix(in srgb, var(--a) 50%, var(--b))" };
    expect(color(t, "--c")).toBe("#000000");
    expect(color(t, "--d")).toBe("#808080");
    expect(() => color(t, "--missing")).toThrow(/not defined/);
    // Light grey on white is the kind of pair this suite exists to catch.
    expect(getContrastRatio("#b8aa96", "#ffffff")).toBeLessThan(AA_TEXT);
  });
});
