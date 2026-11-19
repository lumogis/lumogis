// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Ratchet (LUM-993): from Pass 1 on, new code opens dialogs only through
// src/components/ui/dialog and ui/alert-dialog. The files below still use a
// hand-made dialog, a native <dialog>, window.confirm or div.lumogis-modal; plan B
// moves them over and shrinks this list. Any other file that adds one fails.
// When you move a file over, delete it from the list (the stale check enforces it).

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../../src");

const ALLOWED = new Set<string>([]);

const PATTERN = /role=\{?["']dialog["']|<dialog[\s>]|window\.confirm\s*\(|lumogis-modal/;

export function offenders(files: Record<string, string>): string[] {
  return Object.entries(files)
    .filter(([path, text]) => !path.startsWith("components/ui/") && PATTERN.test(text))
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

describe("dialog ratchet", () => {
  const found = offenders(sources(SRC));

  it("no new file opens a hand-made dialog (use ui/dialog or ui/alert-dialog)", () => {
    expect(found.filter((f) => !ALLOWED.has(f))).toEqual([]);
  });

  it("the allowlist has no stale entries (remove a file once it is moved over)", () => {
    expect([...ALLOWED].filter((f) => !found.includes(f))).toEqual([]);
  });

  it("catches each pattern (the check can fail)", () => {
    expect(
      offenders({
        "features/x/A.tsx": '<div role="dialog">',
        "features/x/B.tsx": "<dialog open>",
        "features/x/C.tsx": "if (window.confirm('Sure?'))",
        "features/x/D.tsx": '<div className="lumogis-modal">',
        "features/x/E.tsx": "<Dialog open>",
        "components/ui/dialog.tsx": '<div role="dialog">',
      }),
    ).toEqual(["features/x/A.tsx", "features/x/B.tsx", "features/x/C.tsx", "features/x/D.tsx"]);
  });
});
