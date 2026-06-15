// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/**
 * WDIO E2E drop-in replacement for `@tauri-apps/api/core`, aliased into the
 * bundle only for the e2e build (see `vite.config.ts`).
 *
 * Why this exists: in embed / production mode Tauri makes
 * `window.__TAURI_INTERNALS__.invoke` a non-writable, non-configurable
 * property, so the WDIO mock layer cannot reassign it at runtime the way it
 * could under `devUrl`. Instead of patching the locked global, we route every
 * app-level `invoke()` through this shim: mocked commands resolve from
 * `window.__wdio_mocks__`, everything else delegates to the real internals
 * invoke (which is exactly what stock `core.invoke` does:
 * `window.__TAURI_INTERNALS__.invoke(cmd, args, options)`).
 *
 * Only `invoke` is consumed from `@tauri-apps/api/core` by the overlay UI; if a
 * future import needs another export, add it here and the build will surface it.
 */

interface WdioCoreWindow {
  __wdio_mocks__?: Record<string, (args?: unknown) => unknown>;
  __TAURI_INTERNALS__?: {
    invoke?: (cmd: string, args?: unknown, options?: unknown) => Promise<unknown>;
  };
}

export async function invoke<T = unknown>(
  cmd: string,
  args?: Record<string, unknown>,
  options?: unknown,
): Promise<T> {
  const w = window as unknown as WdioCoreWindow;
  const mockFn = w.__wdio_mocks__?.[cmd];
  if (typeof mockFn === "function") {
    return (await mockFn(args)) as T;
  }
  const internalsInvoke = w.__TAURI_INTERNALS__?.invoke;
  if (typeof internalsInvoke !== "function") {
    throw new Error(`Tauri internals.invoke unavailable for command "${cmd}"`);
  }
  return internalsInvoke(cmd, args, options) as Promise<T>;
}
