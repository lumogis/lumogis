// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execSync } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));

const appRoot = __dirname;
const tauriDir = join(appRoot, "src-tauri");
const debugBinary = join(tauriDir, "target/debug/lumogis-search");

function assertDebugBinary(): void {
  if (!existsSync(debugBinary)) {
    throw new Error(
      "Lumogis Search debug binary not found — run: " +
        "cd src-tauri && cargo build --features wdio-e2e",
    );
  }
}

assertDebugBinary();
const isSmoke = process.env.OVERLAY_E2E_SMOKE === "1";
const specs = isSmoke ? ["./e2e/smoke/**/*.spec.ts"] : ["./e2e/*.spec.ts"];

export const config = {
  runner: "local",
  onPrepare: async () => {
    const distIndex = join(appRoot, "dist/index.html");
    if (isSmoke) {
      if (!existsSync(distIndex)) {
        execSync("npm run build", { cwd: appRoot, stdio: "inherit" });
      }
    } else {
      // Embed-only: bake E2E dist, merge via tauri.wdio-e2e.conf.json + build.rs, rebuild binary.
      execSync("npm run build:e2e", { cwd: appRoot, stdio: "inherit" });
      // Clean codegen + runtime tauri together — cleaning only lumogis-search leaves a stale
      // tauri.rlib that mismatches freshly generated asset phf maps (embed 404 / blank webview).
      execSync("cargo clean -p lumogis-search -p tauri", { cwd: tauriDir, stdio: "inherit" });
      execSync("cargo build --features wdio-e2e", { cwd: tauriDir, stdio: "inherit" });
    }
  },
  specs,
  maxInstances: 1,
  logLevel: "info",
  bail: 0,
  waitforTimeout: 10_000,
  connectionRetryTimeout: 120_000,
  connectionRetryCount: 3,
  autoXvfb: false,
  services: [
    [
      "@wdio/tauri-service",
      {
        driverProvider: "embedded",
        appBinaryPath: debugBinary,
        captureBackendLogs: true,
        captureFrontendLogs: true,
      },
    ],
  ],
  capabilities: [
    {
      browserName: "tauri",
      "wdio:enforceWebDriverClassic": true,
      "tauri:options": {
        application: debugBinary,
      },
      "wdio:tauriServiceOptions": {
        appBinaryPath: debugBinary,
        driverProvider: "embedded",
        captureBackendLogs: true,
        captureFrontendLogs: true,
      },
    },
  ],
  framework: "mocha",
  reporters: ["spec"],
  mochaOpts: {
    ui: "bdd",
    timeout: 120_000,
    require: ["./e2e/wdio-setup.ts"],
  },
  autoCompileOpts: {
    autoCompile: true,
    tsNodeOpts: {
      transpileOnly: true,
      project: "./tsconfig.e2e.json",
    },
  },
};
