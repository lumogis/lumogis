// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Runs `scripts/check-pwa-dist.mjs <dir>` against fixture dists to prove the precache guardrail
 * (an empty or un-injected service-worker manifest must fail the check).
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const script = path.join(__dirname, "..", "..", "scripts", "check-pwa-dist.mjs");

const LISTENERS = `self.addEventListener("push",()=>{});self.addEventListener("notificationclick",()=>{});`;
const MANIFEST_OK = `[{"revision":"abc","url":"index.html"},{"revision":null,"url":"assets/index-Be3K7cS1.js"}]`;
const GOOD_SW = `${LISTENERS}precacheAndRoute(${MANIFEST_OK});`;

const made: string[] = [];

function makeDist(swBody: string): string {
  const dir = mkdtempSync(path.join(tmpdir(), "lumogis-pwa-dist-"));
  made.push(dir);
  mkdirSync(path.join(dir, "icons"));
  writeFileSync(path.join(dir, "sw.js"), swBody);
  writeFileSync(path.join(dir, "manifest.webmanifest"), "{}");
  writeFileSync(path.join(dir, "icons", "icon-192.png"), "");
  writeFileSync(path.join(dir, "icons", "icon-512.png"), "");
  return dir;
}

function run(dir: string) {
  return spawnSync(process.execPath, [script, dir], { encoding: "utf8" });
}

afterEach(() => {
  while (made.length) rmSync(made.pop()!, { recursive: true, force: true });
});

describe("check-pwa-dist.mjs precache guardrail", () => {
  it("passes on a complete dist", () => {
    const r = run(makeDist(GOOD_SW));
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("non-empty precache");
  });

  it("fails when the manifest is empty", () => {
    const r = run(makeDist(`${LISTENERS}precacheAndRoute([]);`));
    expect(r.status).toBe(1);
    expect(r.stderr).toContain("no JS asset");
  });

  it("fails when the injection point survives", () => {
    const r = run(makeDist(`${GOOD_SW}precacheAndRoute(self.__WB_MANIFEST);`));
    expect(r.status).toBe(1);
    expect(r.stderr).toContain("not injected");
  });

  it("fails when index.html is missing from the manifest", () => {
    const r = run(
      makeDist(`${LISTENERS}precacheAndRoute([{"revision":null,"url":"assets/index-Be3K7cS1.js"}]);`),
    );
    expect(r.status).toBe(1);
    expect(r.stderr).toContain("missing index.html");
  });

  it("accepts unquoted keys and single quotes (format-tolerant)", () => {
    const r = run(
      makeDist(`${LISTENERS}precacheAndRoute([{revision:'a',url:'index.html'},{revision:null,url:'assets/x.js'}]);`),
    );
    expect(r.status).toBe(0);
  });

  it("still fails on runtimeCaching", () => {
    const r = run(makeDist(`${GOOD_SW}const runtimeCaching=[];`));
    expect(r.status).toBe(1);
    expect(r.stderr).toContain("runtimeCaching");
  });
});
