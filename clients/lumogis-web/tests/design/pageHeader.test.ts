// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Every Settings page has exactly one header, rendered by the frame (LUM-993
// Pass 3): SettingsPage renders <PageHeader> for every entry in settingsNav, so
// the views themselves must not render an <h1> or a PageHeader of their own.
// A source scan in the ADR 222 style; the fixtures prove it can fail.

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, "..", "..", "src");
const read = (rel: string) => readFileSync(path.join(src, rel), "utf8");
/** Source without comments, so a comment that mentions <h1> is not a heading. */
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

/** Views settingsNav renders: every `../area/File` import in settingsNav.ts. */
function settingsViewFiles(): string[] {
  const nav = read("features/settings/settingsNav.ts");
  return [...nav.matchAll(/from "\.\.\/(admin|me|signals|audit)\/(\w+)";/g)].map((m) => `features/${m[1]}/${m[2]}.tsx`);
}

/** Problems in one view's source: its own page title. */
export function headerProblems(source: string): string[] {
  const out: string[] = [];
  const c = code(source);
  if (/<h1[\s>]/.test(c)) out.push("renders an <h1>");
  if (/<PageHeader[\s/>]/.test(c)) out.push("renders its own PageHeader");
  return out;
}

describe("Settings page headers", () => {
  it("SettingsPage renders the one PageHeader, with the entry's title and help", () => {
    const page = read("features/settings/SettingsPage.tsx");
    expect(page).toMatch(/<PageHeader[\s\S]*?title=\{entry\.title\}/);
    expect(page).toMatch(/description=\{copy\.page\}/);
    expect(page).toMatch(/info=\{copy\.about\}/);
  });

  it("no Settings view renders an <h1> or a PageHeader of its own", () => {
    const files = settingsViewFiles();
    expect(files.length).toBeGreaterThanOrEqual(30);
    const problems = files.flatMap((f) => headerProblems(read(f)).map((p) => `${f}: ${p}`));
    expect(problems).toEqual([]);
  });

  it("no file under the old Settings folders renders a bare <h1>", () => {
    const offenders: string[] = [];
    for (const dir of ["features/me", "features/admin", "features/signals", "features/audit", "features/settings"]) {
      for (const f of readdirSync(path.join(src, dir))) {
        if (f.endsWith(".tsx") && /<h1[\s>]/.test(code(read(`${dir}/${f}`)))) offenders.push(`${dir}/${f}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("catches a view with its own title (the check can fail)", () => {
    expect(headerProblems('<section><h1>Users</h1></section>')).toEqual(["renders an <h1>"]);
    expect(headerProblems('<PageHeader title="Users" />')).toEqual(["renders its own PageHeader"]);
    expect(headerProblems("<section><h2>Invites</h2></section>")).toEqual([]);
    expect(headerProblems("// the frame renders the <h1>\n<h2>Invites</h2>")).toEqual([]);
  });
});
