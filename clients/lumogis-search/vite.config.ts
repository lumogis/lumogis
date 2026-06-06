// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { defineConfig } from "vite";

const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  root: "ui",
  clearScreen: false,
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
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    target: process.env.TAURI_ENV_PLATFORM === "windows" ? "chrome105" : "safari14",
    minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
  },
});
