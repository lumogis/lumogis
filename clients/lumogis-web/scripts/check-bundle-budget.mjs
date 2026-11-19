#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Bundle budget (LUM-993 Decision 14), run after the web build:
//   node scripts/check-bundle-budget.mjs [--dist dist]
// Gzips every dist/assets/*.js and *.css and fails when the entry script (the
// module script in dist/index.html), all JS together, or all CSS together is
// over budget. Prints the numbers for the plan's Implementation Log.
// On a miss, the plan's remedy is lazy-loading Settings and admin pages.

import fs from "node:fs";
import path from "node:path";
import { gzipSync } from "node:zlib";

const KB = 1024;
// Entry raised from 320 to 400 KB: Thomas accepted a larger first load for the new
// components (LUM-993, 18 Nov 2026). Still a tripwire: past these, decide on purpose.
export const BUDGET = { entryJs: 400 * KB, allJs: 400 * KB, allCss: 25 * KB };

const argv = process.argv.slice(2);
const distAt = argv.indexOf("--dist");
const dist = path.resolve(distAt >= 0 && argv[distAt + 1] ? argv[distAt + 1] : "dist");
const assets = path.join(dist, "assets");
if (!fs.existsSync(assets)) {
  console.error(`check-bundle-budget: ${assets} not found; run the web build first`);
  process.exit(2);
}

const gz = (file) => gzipSync(fs.readFileSync(file), { level: 9 }).length;
const files = fs.readdirSync(assets);
const js = files.filter((f) => f.endsWith(".js"));
const css = files.filter((f) => f.endsWith(".css"));

const html = fs.readFileSync(path.join(dist, "index.html"), "utf8");
const entryMatch = /<script[^>]*type="module"[^>]*src="[^"]*\/assets\/([^"]+\.js)"/.exec(html);
if (!entryMatch) {
  console.error("check-bundle-budget: no module script in dist/index.html");
  process.exit(2);
}
const entry = entryMatch[1];

const sizes = Object.fromEntries([...js, ...css].map((f) => [f, gz(path.join(assets, f))]));
const total = (list) => list.reduce((n, f) => n + sizes[f], 0);
const measured = { entryJs: sizes[entry], allJs: total(js), allCss: total(css) };
const label = { entryJs: `entry JS (${entry})`, allJs: `all JS (${js.length} files)`, allCss: `all CSS (${css.length} files)` };

let failed = false;
for (const key of Object.keys(BUDGET)) {
  const over = measured[key] > BUDGET[key];
  failed ||= over;
  console.log(
    `${over ? "OVER" : "ok  "}  ${label[key].padEnd(44)} ${(measured[key] / KB).toFixed(1).padStart(7)} KB gzip  (budget ${BUDGET[key] / KB} KB)`,
  );
}
const largest = [...js].sort((a, b) => sizes[b] - sizes[a]).slice(0, 5);
console.log(`largest JS: ${largest.map((f) => `${f} ${(sizes[f] / KB).toFixed(1)} KB`).join(", ")}`);
process.exit(failed ? 1 : 0);
