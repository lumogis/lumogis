// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { type ChildProcess, execSync, spawn } from "node:child_process";
import { createConnection } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const appRoot = join(__dirname, "..");
const DEV_PORT = 1421;

let child: ChildProcess | null = null;

function freeDevPort(port: number): void {
  try {
    execSync(`fuser -k ${port}/tcp 2>/dev/null || true`, { stdio: "ignore" });
  } catch {
    /* best-effort */
  }
}

function waitForPort(port: number, timeoutMs = 60_000): Promise<void> {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = (): void => {
      const socket = createConnection({ port, host: "127.0.0.1" });
      socket.once("connect", () => {
        socket.end();
        resolve();
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() - start > timeoutMs) {
          reject(new Error(`Vite preview server did not start on :${port} within ${timeoutMs}ms`));
          return;
        }
        setTimeout(tryOnce, 250);
      });
    };
    tryOnce();
  });
}

/**
 * Serve the E2E `dist` on :1421 — debug binaries load `devUrl` (http://localhost:1421).
 * Uses `vite preview` (not dev) so `VITE_WDIO_E2E` from `build:e2e` is baked into the bundle.
 */
export async function startOverlayDevServer(): Promise<void> {
  if (child) {
    return;
  }
  freeDevPort(DEV_PORT);
  child = spawn("npx", ["vite", "preview", "--port", String(DEV_PORT), "--strictPort"], {
    cwd: appRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout?.on("data", (chunk: Buffer) => {
    process.stdout.write(`[overlay-vite] ${chunk}`);
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    process.stderr.write(`[overlay-vite] ${chunk}`);
  });
  await waitForPort(DEV_PORT);
}

export async function stopOverlayDevServer(): Promise<void> {
  if (!child) {
    return;
  }
  child.kill("SIGTERM");
  child = null;
}
