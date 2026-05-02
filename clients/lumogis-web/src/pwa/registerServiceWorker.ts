// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

/**
 * Register the Lumogis Web service worker (`/sw.js`) for production installs only.
 * Skips registration in Vitest/unit tests and during `vite dev` (same-origin SPA without SW).
 * Failures are non-fatal; update prompting remains minimal (future work).
 */

const SW_SCRIPT = "/sw.js";

export function registerLumogisServiceWorker(): void {
  if (typeof navigator === "undefined") return;
  if (!import.meta.env.PROD) return;
  if (!("serviceWorker" in navigator)) return;

  navigator.serviceWorker.register(SW_SCRIPT, {scope: "/", type: "module"}).catch(() => {
    console.info("[lumogis-web] Service worker registration failed (ignored).");
  });
}
