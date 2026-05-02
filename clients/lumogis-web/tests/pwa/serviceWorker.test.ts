// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Phase 3B guardrails — static precache via Workbox injectManifest only (no runtime API caches).
 */
import {readFileSync} from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

import {describe, expect, it} from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const clientRoot = path.join(__dirname, "..", "..");

function read(rel: string): string {
  return readFileSync(path.join(clientRoot, rel), "utf8");
}

describe("Workbox SW source (injectManifest)", () => {
  it("imports precaching only (no routing runtime)", () => {
    const src = read("src/pwa/sw.ts");
    expect(src).toMatch(/precacheAndRoute/);
    expect(src).toMatch(/cleanupOutdatedCaches/);
    expect(src).not.toMatch(/registerRoute\b/);
    expect(src).not.toMatch(/registerNavigationRoute\b/);
    expect(src).not.toMatch(/navigationPreload\b/);
    expect(src).not.toMatch(/routing\.registerRoute/);
    expect(src).not.toMatch(/StaleWhileRevalidate|NetworkOnly|NetworkFirst|CacheFirst/i);
    expect(src).not.toMatch(/runtimeCaching\b/);
  });

  it("Phase 4D registers push + notificationclick (no fetch/Cache additions for APIs)", () => {
    const src = read("src/pwa/sw.ts");
    expect(src).toMatch(/addEventListener\s*\(\s*["']push["']/);
    expect(src).toMatch(/addEventListener\s*\(\s*["']notificationclick["']/);
    expect(src).not.toMatch(/\bfetch\s*\(/);
    expect(src).not.toMatch(/\bcaches\./);
  });

  it("pulls Phase 4D helpers from swPush.ts (tested in swPayload.test.ts)", () => {
    expect(read("src/pwa/sw.ts")).toMatch(/\.\/swPush\b/);
  });

  it("does not spell private routes that must never appear in SW caching logic", () => {
    const src = read("src/pwa/sw.ts");
    expect(src).not.toMatch(/\/api\//);
    expect(src).not.toMatch(/\/events/);
    expect(src).not.toMatch(/\/v1\//);
    expect(src).not.toMatch(/\/auth\b/);
  });
});

describe("Vite PWA plugin configuration", () => {
  it("uses injectManifest and omits runtime caching", () => {
    const cfg = read("vite.config.ts");
    expect(cfg).toMatch(/strategies:\s*"injectManifest"/);
    expect(cfg).toMatch(/injectManifest\s*:/);
    expect(cfg).toMatch(/VitePWA\(/);
    expect(cfg.toLowerCase()).not.toMatch(/\bruntimeCaching\b/i);
    expect(cfg).not.toMatch(/generateSW\b/);
  });
});

describe("Client registration helper", () => {
  it("registers production-only and ignores failures gracefully", () => {
    const reg = read("src/pwa/registerServiceWorker.ts");
    expect(reg).toMatch(/import\.meta\.env\.PROD/);
    expect(reg).toMatch(/serviceWorker\s*\.\s*register\b/);
    expect(reg).toMatch(/\[lumogis-web\] Service worker registration failed/);
  });
});
