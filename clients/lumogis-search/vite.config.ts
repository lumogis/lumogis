// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";

const host = process.env.TAURI_DEV_HOST;
const isE2eBuild = process.env.VITE_WDIO_E2E === "true";
const uiDir = fileURLToPath(new URL("./ui", import.meta.url));

export default defineConfig({
  root: "ui",
  // Relative asset URLs — required for Tauri `asset://` / custom-protocol loads in production dist.
  base: "./",
  clearScreen: false,
  define: {
    "import.meta.env.VITE_WDIO_E2E": JSON.stringify(process.env.VITE_WDIO_E2E === "true" ? "true" : ""),
  },
  server: {
    port: 1421,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
  },
  envPrefix: ["VITE_", "TAURI_"],
  resolve: isE2eBuild
    ? {
        // Embed/production mode locks `__TAURI_INTERNALS__.invoke`, so the WDIO
        // mock layer cannot reassign it. Route the app's `invoke()` through a
        // shim that consults `window.__wdio_mocks__` first. Anchored regex so
        // it matches only the bare specifier, never the package's relative
        // `./core.js` internals.
        alias: [
          {
            find: /^@tauri-apps\/api\/core$/,
            replacement: resolve(uiDir, "e2e-tauri-core.shim.ts"),
          },
        ],
      }
    : undefined,
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    target: process.env.TAURI_ENV_PLATFORM === "windows" ? "chrome105" : "safari14",
    minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
    rollupOptions: isE2eBuild
      ? {
          input: resolve(uiDir, "index.e2e.html"),
          output: {
            // Single bundle — Tauri CSP `connect-src 'none'` blocks dynamic chunk fetch.
            inlineDynamicImports: true,
          },
        }
      : undefined,
  },
});
