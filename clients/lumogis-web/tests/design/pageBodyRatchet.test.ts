// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Ratchet (LUM-993 plan B): page bodies are built from the shared components.
// The files below still use an inline style={{…}}, a raw <button> or
// window.confirm; each pass of plan B moves some over and deletes them here.
// Any other file under src/features that adds one fails. When you move a file
// over, delete it from the list (the stale check enforces it). Plan B Pass 8
// ends with an empty list.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../../src");

const ALLOWED = new Set<string>([]);

const PATTERN = /style=\{\{|<button[\s>]|window\.confirm\s*\(/;

export function offenders(files: Record<string, string>): string[] {
  return Object.entries(files)
    .filter(([path, text]) => path.startsWith("features/") && path.endsWith(".tsx") && PATTERN.test(text))
    .map(([path]) => path)
    .sort();
}

function sources(dir: string, out: Record<string, string> = {}): Record<string, string> {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.(tsx?|jsx?)$/.test(name)) out[relative(SRC, full).split("\\").join("/")] = readFileSync(full, "utf8");
  }
  return out;
}

describe("page body ratchet", () => {
  const found = offenders(sources(SRC));

  it("no new page body uses inline styles, raw buttons or window.confirm", () => {
    expect(found.filter((f) => !ALLOWED.has(f))).toEqual([]);
  });

  it("the allowlist has no stale entries (remove a file once it is moved over)", () => {
    expect([...ALLOWED].filter((f) => !found.includes(f))).toEqual([]);
  });

  it("catches each pattern (the check can fail)", () => {
    expect(
      offenders({
        "features/x/A.tsx": '<div style={{ color: "red" }}>',
        "features/x/B.tsx": '<button type="button">',
        "features/x/C.tsx": "if (window.confirm('Sure?'))",
        "features/x/D.tsx": '<Button type="button">',
        "components/ui/button.tsx": "<button>",
      }),
    ).toEqual(["features/x/A.tsx", "features/x/B.tsx", "features/x/C.tsx"]);
  });
});
