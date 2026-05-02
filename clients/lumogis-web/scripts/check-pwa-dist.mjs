#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/** Run after `npm run build` to assert Phase 3A+3B(+4D) static artefacts exist. */

import {existsSync, readFileSync} from "node:fs";
import path from "node:path";
import process from "node:process";
import {fileURLToPath} from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

const req = ["dist/sw.js", "dist/manifest.webmanifest", "dist/icons/icon-192.png", "dist/icons/icon-512.png"];

let ok = true;
for (const rel of req) {
  const p = path.join(root, rel);
  if (!existsSync(p)) {
    console.error(`missing: ${rel}`);
    ok = false;
  }
}

if (!ok) process.exit(1);

const swPath = path.join(root, "dist/sw.js");
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

if (!ok) process.exit(1);
console.log("PWA dist check OK:", req.join(", "), "+ Phase 4D listeners, no runtimeCaching");
