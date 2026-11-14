// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-742 — the guard for the defect class that produced this ticket.
//
// Five components under `features/email/` shipped with no production importer.
// Each had tests, and every one of those tests passed, because they render the
// component directly. That is the gap render tests cannot see by construction:
// a component is perfectly renderable and perfectly unreachable at the same
// time. One of the five was the consent toggle the entire attachment feature
// gates on, so the feature was inert in production while six tickets were
// closed against it.
//
// This asserts the IMPORT GRAPH instead. It is deliberately not a `includes(name)`
// substring scan: a mention in a comment would count as an importer and the guard
// would pass while the orphaning returned. Only `import ... from "..."` and
// `export ... from "..."` specifiers count, resolved to real files under `src/`.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../../../src");

// LUM-793 extended this from `features/email` alone. The guard's argument —
// "render tests are what let five of these ship unreachable" — is about the
// defect class, not about one directory, and the failed-send recovery surface
// is reachable from exactly one page. A directory constant is the whole cost of
// covering it, and the plan's DoD row asking for coverage was unsatisfiable
// while this list had one entry.
const GUARDED_DIRS = [
  { rel: "features/email", min: 5 },
  { rel: "features/approvals", min: 2 },
] as const;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const p = join(dir, entry);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

/** Module specifiers this file imports or re-exports from. Statements only. */
function specifiersOf(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const out: string[] = [];
  // `import x from "y"`, `import "y"`, `export { x } from "y"`, and the
  // dynamic `import("y")` form. Nothing else counts as a dependency edge.
  const patterns = [
    /\bimport\s[^;]*?\sfrom\s*["']([^"']+)["']/g,
    /\bimport\s*["']([^"']+)["']/g,
    /\bexport\s[^;]*?\sfrom\s*["']([^"']+)["']/g,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const re of patterns) {
    for (const m of text.matchAll(re)) out.push(m[1]);
  }
  return out;
}

/** Resolve a relative specifier to a concrete file under `src/`, or null. */
function resolveSpecifier(fromFile: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null;
  const base = resolve(dirname(fromFile), spec);
  for (const cand of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
    try {
      if (statSync(cand).isFile()) return cand;
    } catch {
      /* not this one */
    }
  }
  return null;
}

const productionFiles = walk(SRC).filter((f) => /\.(ts|tsx)$/.test(f));

/** Every production file that imports `target`, excluding the target itself. */
function importersOf(target: string): string[] {
  return productionFiles.filter(
    (f) =>
      f !== target &&
      specifiersOf(f).some((spec) => resolveSpecifier(f, spec) === target),
  );
}

describe.each(GUARDED_DIRS)("every $rel surface is reachable from production code", ({ rel: dirRel, min }) => {
  const dir = join(SRC, dirRel);
  const components = readdirSync(dir)
    .filter((f) => f.endsWith(".tsx"))
    .map((f) => join(dir, f));

  it("finds components to check (guards against a silently empty sweep)", () => {
    // A guard that checks nothing is worse than no guard, because it reports
    // green. If the directory moves, this fails rather than passing vacuously.
    expect(components.length).toBeGreaterThanOrEqual(min);
  });

  it.each(components.map((c) => [relative(SRC, c), c] as const))(
    "%s has at least one production importer",
    (rel, file) => {
      const importers = importersOf(file);
      expect(
        importers.length,
        `${rel} is imported by nothing under src/. It may still be fully tested — ` +
          `that is exactly how five of these shipped unreachable. Mount it on a ` +
          `route or a surface a user can get to, or delete it.`,
      ).toBeGreaterThan(0);
    },
  );
});

describe("the guard itself", () => {
  it("does not count a name merely mentioned in a comment", () => {
    // The naive implementation of this guard is `text.includes(name)`, which
    // would pass on any file that happens to mention the component — including
    // the ticket references scattered through this codebase's comments.
    const fake = join(SRC, "features/email", "AttachmentReviewList.tsx");
    const mentionsOnly = productionFiles.filter((f) => {
      if (f === fake) return false;
      const text = readFileSync(f, "utf8");
      return text.includes("AttachmentReviewList") && !importersOf(fake).includes(f);
    });
    // Whatever those files are, the guard must not have counted them.
    for (const f of mentionsOnly) {
      expect(importersOf(fake)).not.toContain(f);
    }
  });
});
