// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Validates PWA installability metadata (Phase 3A) and confines SW tooling to `src/pwa`.
 */
import { readdirSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const clientRoot = path.join(__dirname, "..", "..");
const manifestPath = path.join(clientRoot, "public", "manifest.webmanifest");

function pngDimensions(bytes: Uint8Array): { width: number; height: number } {
  expect(bytes.slice(0, 8)).toEqual(
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  );
  expect(Buffer.from(bytes.slice(12, 16)).toString("ascii")).toBe("IHDR");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(16);
  const height = view.getUint32(20);
  return { width, height };
}

function readTxtUnderSrc(rel: string): string {
  return readFileSync(path.join(clientRoot, "src", rel), "utf8");
}

describe("PWA manifest (Phase 3A)", () => {
  it("is valid JSON with required fields and local icons", async () => {
    const raw = await readFile(manifestPath, "utf8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;

    expect(parsed.name).toBe("Lumogis");
    expect(parsed.short_name).toBe("Lumogis");
    expect(parsed.display).toBe("standalone");
    expect(parsed.scope).toBe("/");
    expect(parsed.start_url).toBe("/chat");
    expect(typeof parsed.theme_color).toBe("string");
    expect(parsed.theme_color).toBeTruthy();
    expect(typeof parsed.background_color).toBe("string");
    expect(parsed.background_color).toBeTruthy();

    const icons = parsed.icons as Array<Record<string, unknown>>;
    expect(Array.isArray(icons)).toBe(true);
    const specs = icons.map((i) => ({
      sizes: i.sizes,
      purpose: String(i.purpose ?? ""),
      src: i.src as string,
    }));
    expect(specs.some((s) => s.sizes === "192x192" && String(s.src).startsWith("/icons/"))).toBe(true);
    expect(specs.some((s) => s.sizes === "512x512" && String(s.src).startsWith("/icons/"))).toBe(true);
    expect(specs.every((s) => s.purpose.includes("maskable"))).toBe(true);
    expect(icons.every((i) => !String(i.src).includes("://"))).toBe(true);
  });

  it("includes Quick capture shortcut and GET share_target to /capture", async () => {
    const raw = await readFile(manifestPath, "utf8");
    const parsed = JSON.parse(raw) as {
      shortcuts?: Array<{ name?: string; url?: string }>;
      share_target?: { action?: string; method?: string; params?: Record<string, string> };
    };

    const captureShortcut = parsed.shortcuts?.find((s) => s.url === "/capture");
    expect(captureShortcut?.name).toMatch(/quick capture/i);

    expect(parsed.share_target?.action).toBe("/capture");
    expect(parsed.share_target?.method).toBe("GET");
    expect(parsed.share_target?.params?.text).toBe("text");
    expect(parsed.share_target?.params?.title).toBe("title");
    expect(parsed.share_target?.params?.url).toBe("url");
  });

  it("icon files exist and match declared dimensions", async () => {
    for (const name of ["icon-192.png", "icon-512.png"] as const) {
      const p = path.join(clientRoot, "public", "icons", name);
      const buf = await readFile(p);
      const { width, height } = pngDimensions(buf);
      if (name === "icon-192.png") {
        expect(width).toBe(192);
        expect(height).toBe(192);
      } else {
        expect(width).toBe(512);
        expect(height).toBe(512);
      }
    }
  });
});

describe("Service worker scope hygiene (Phase 3B)", () => {
  it("mentions serviceWorker/workbox/registerSW only inside src/pwa", () => {
    const hits: string[] = [];
    const walk = (dir: string, rel = ""): void => {
      for (const ent of readdirSync(dir, { withFileTypes: true })) {
        if (ent.name === "__tests__" || ent.name === "generated") continue;
        const r = path.join(rel, ent.name);
        const full = path.join(dir, ent.name);
        if (ent.isDirectory()) {
          walk(full, r);
        } else if (/\.(tsx?|jsx?)$/.test(ent.name)) {
          const text = readFileSync(full, "utf8");
          if (!r.startsWith("pwa")) {
            if (/\bserviceWorker\b/i.test(text) || /registerSW/i.test(text) || /workbox/i.test(text)) {
              hits.push(`src/${r}`);
            }
          }
        }
      }
    };
    walk(path.join(clientRoot, "src"));
    expect(hits).toEqual([]);
  });

  it("imports registration helper from main (prod registration only)", () => {
    const main = readTxtUnderSrc("main.tsx");
    expect(main).toMatch(/\.\/pwa\/registerServiceWorker/);
  });
});
