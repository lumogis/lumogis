#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/** Run after `npm run build` to assert Phase 3A+3B(+4D) static artefacts exist. */

import {existsSync, readFileSync} from "node:fs";
import path from "node:path";
import process from "node:process";
import {fileURLToPath} from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Optional first argument: dist directory to check (default `dist`; tests pass a fixture). */
const distDir = process.argv[2] ? path.resolve(process.argv[2]) : path.join(root, "dist");

const req = ["sw.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

let ok = true;
for (const rel of req) {
  const p = path.join(distDir, rel);
  if (!existsSync(p)) {
    console.error(`missing: dist/${rel}`);
    ok = false;
  }
}

if (!ok) process.exit(1);

const swPath = path.join(distDir, "sw.js");
const swJs = readFileSync(swPath, "utf8");

/** Precache/workbox internals may reference routing helpers; Phase 4D requires push handlers; forbid Vite `runtimeCaching`. */
const mustHavePush = /\baddEventListener\s*\(\s*["']push["']/;
const mustHaveClick = /\baddEventListener\s*\(\s*["']notificationclick["']/;

if (!mustHavePush.test(swJs)) {
  console.error('dist/sw.js: expected Phase 4D push listener (addEventListener "push")');
  ok = false;
}
if (!mustHaveClick.test(swJs)) {
  console.error("dist/sw.js: expected Phase 4D notificationclick listener");
  ok = false;
}

/** Vite PWA `runtimeCaching[]` echoes this identifier; lumogis-web uses precache-only `injectManifest`. */
if (/\bruntimeCaching\b/.test(swJs)) {
  console.error("dist/sw.js: forbid runtimeCaching (Phase 3/4 boundary — precache-only)");
  ok = false;
}

/** Guardrail: accidental API route caching in SW source (distinct from Workbox precache of hashed filenames). */
if (swJs.includes('/api/"') || swJs.includes("/api/'")) {
  console.error("dist/sw.js: unexpected quoted /api/ route pattern");
  ok = false;
}

/**
 * Precache guardrail: an empty manifest builds and installs fine but caches nothing (workbox#3594,
 * a glob/minimatch major mismatch). The regexes tolerate quoted or unquoted keys, either quote style
 * and whitespace, so a formatting-only change in the injection output is not a regression.
 */
if (swJs.includes("self.__WB_MANIFEST")) {
  console.error("dist/sw.js: precache manifest not injected (self.__WB_MANIFEST survived the build)");
  ok = false;
}
if (!/["']?url["']?\s*:\s*["']index\.html["']/.test(swJs)) {
  console.error("dist/sw.js: precache manifest missing index.html");
  ok = false;
}
if (!/["']?url["']?\s*:\s*["']assets\/[^"']+\.js["']/.test(swJs)) {
  console.error("dist/sw.js: precache manifest has no JS asset — empty manifest? see workbox#3594");
  ok = false;
}

if (!ok) process.exit(1);
console.log(
  "PWA dist check OK:",
  req.map((r) => `dist/${r}`).join(", "),
  "+ Phase 4D listeners, no runtimeCaching, + non-empty precache",
);
