// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Help copy keeps Thomas's style (LUM-993): one or two short sentences, plain
// words, no developer leftovers. A fixture proves the check can fail.

import { describe, expect, it } from "vitest";

import { help } from "../../src/copy/help";

const MAX_WORDS = 30;
const FORBIDDEN: Array<[RegExp, string]> = [
  [/`/, "backtick"],
  [/\/api\//, "API path"],
  [/\bmake\s/, "make command"],
  [/\bpython\s/, "python command"],
  [/\b[a-z]+_[a-z_]+\b/i, "snake_case id"],
];

function problems(text: string): string[] {
  const out: string[] = [];
  const sentences = text.split(/(?<=[.!?])\s+/).filter((s) => s.trim().length > 0);
  if (sentences.length < 1 || sentences.length > 2) out.push(`${sentences.length} sentences`);
  const words = text.trim().split(/\s+/).length;
  if (words > MAX_WORDS) out.push(`${words} words`);
  if (!/[.!?]$/.test(text.trim())) out.push("no closing full stop");
  for (const [re, name] of FORBIDDEN) if (re.test(text)) out.push(name);
  return out;
}

function entries(): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const [page, keys] of Object.entries(help)) {
    for (const [key, text] of Object.entries(keys as Record<string, string>)) out.push([`${page}.${key}`, text]);
  }
  return out;
}

describe("help copy", () => {
  it("is not empty", () => {
    expect(entries().length).toBeGreaterThanOrEqual(5);
  });

  it.each(entries())("%s follows the house style", (_key, text) => {
    expect(problems(text)).toEqual([]);
  });

  it("rejects a developer-style text (the check can fail)", () => {
    expect(problems("Run make backup.")).toContain("make command");
    expect(problems("Set the WEBPUSH_VAPID_KEY. Then restart. Then reload.")).toEqual(
      expect.arrayContaining(["3 sentences", "snake_case id"]),
    );
    expect(problems("No full stop here")).toContain("no closing full stop");
  });
});
