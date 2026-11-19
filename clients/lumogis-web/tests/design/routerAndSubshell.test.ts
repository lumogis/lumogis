// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Two layout regressions found in the LUM-993 walkthrough, guarded at the source
// because jsdom neither follows a redirect loop the way a browser does nor applies CSS.
//
// 1. A catch-all route redirected with a relative target ("profile"). Inside a splat
//    route React Router resolves it against the unknown path itself, so
//    /me/secure-address became /me/secure-address/profile/profile/… and showed a
//    blank page. Catch-all redirects must be absolute.
// 2. .lumogis-subshell was its own container and queried itself. A container query
//    cannot restyle the element it measures, so Settings and Admin never stacked on
//    a phone. The container must be the wrapper. Since LUM-993 Pass 3 the one
//    Settings layout carries the same rule (Tailwind's @container/settings).

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const clientRoot = path.join(__dirname, "..", "..");
const read = (rel: string) => readFileSync(path.join(clientRoot, rel), "utf8");

describe("catch-all redirects", () => {
  it("use absolute targets", () => {
    const app = read("src/App.tsx");
    const splats = [...app.matchAll(/<Route\s+path="\*"\s+element=\{<Navigate\s+to="([^"]+)"/g)].map((m) => m[1]);
    expect(splats.length).toBeGreaterThanOrEqual(2); // /settings/* and the app-level fallback
    for (const target of splats) expect(target.startsWith("/")).toBe(true);
  });
});

describe("Settings layout container", () => {
  const layout = read("src/features/settings/SettingsLayout.tsx");
  // Each element's className, in source order.
  const classNames = [...layout.matchAll(/className="([^"]*)"/g)].map((m) => m[1] ?? "");

  it("puts the container on the wrapper, not on the grid it restyles", () => {
    const container = classNames.findIndex((c) => c.split(/\s+/).includes("@container/settings"));
    const grid = classNames.findIndex((c) => c.includes("@min-[720px]/settings:grid"));
    expect(container).toBeGreaterThan(-1);
    expect(grid).toBeGreaterThan(container);
    expect(classNames[grid]).not.toContain("@container/settings");
  });
});
