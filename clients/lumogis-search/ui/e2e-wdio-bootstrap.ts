// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/**
 * WDIO E2E — @wdio/tauri-plugin + patch for @tauri-apps/api v2 invoke path.
 * Tauri 2's bundled `invoke()` calls `window.__TAURI_INTERNALS__.invoke`, not
 * `window.__TAURI__.core.invoke` (what the stock plugin patches).
 */

try {
  document.documentElement.dataset.e2eBootstrap = "loaded";
  window.addEventListener("error", (ev) => {
    document.documentElement.dataset.lumogisBootError = String(
      (ev as ErrorEvent).message ?? ev,
    ).slice(0, 200);
  });
  window.addEventListener("unhandledrejection", (ev) => {
    document.documentElement.dataset.lumogisBootError =
      "rejection: " + String((ev as PromiseRejectionEvent).reason).slice(0, 200);
  });
} catch {
  /* sentinel */
}

type WdioCoreWindow = Window & {
  __TAURI__?: { core?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> } };
  __TAURI_INTERNALS__?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> };
  __wdio_original_core__?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> };
  __wdio_original_tauri__?: unknown;
};

/** Snapshot real invoke before the plugin installs its Proxy (direct-eval mock path). */
function primeWdioOriginalCoreOnce(): boolean {
  const w = window as WdioCoreWindow;
  if (w.__wdio_original_core__?.invoke) {
    return true;
  }
  const core = w.__TAURI__?.core;
  if (core && typeof core.invoke === "function") {
    w.__wdio_original_tauri__ = w.__TAURI__;
    w.__wdio_original_core__ = core;
    document.documentElement.dataset.wdioCorePrimed = "1";
    return true;
  }
  const internalsInvoke = w.__TAURI_INTERNALS__?.invoke;
  if (typeof internalsInvoke === "function") {
    w.__wdio_original_core__ = {
      invoke: internalsInvoke.bind(w.__TAURI_INTERNALS__),
    };
    document.documentElement.dataset.wdioCorePrimed = "internals";
    return true;
  }
  return false;
}

function startCorePrimeLoop(): void {
  if (primeWdioOriginalCoreOnce()) {
    return;
  }
  let attempts = 0;
  const tick = (): void => {
    attempts += 1;
    if (primeWdioOriginalCoreOnce()) {
      return;
    }
    if (attempts < 200) {
      window.setTimeout(tick, 50);
    }
  };
  tick();
}

startCorePrimeLoop();

import "@wdio/tauri-plugin";

type WdioMocksWindow = Window & {
  __TAURI_INTERNALS__?: {
    invoke?: (cmd: string, args?: unknown, options?: unknown) => Promise<unknown>;
    _wdioInvokePatched?: boolean;
  };
  __wdio_mocks__?: Record<string, (...args: unknown[]) => unknown>;
  __wdio_original_internals_invoke__?: (
    cmd: string,
    args?: unknown,
    options?: unknown,
  ) => Promise<unknown>;
};

function patchInternalsInvoke(): boolean {
  const w = window as WdioMocksWindow;
  const internals = w.__TAURI_INTERNALS__;
  if (!internals || typeof internals.invoke !== "function") {
    return false;
  }
  if (internals._wdioInvokePatched) {
    return true;
  }

  const base = internals.invoke.bind(internals);
  w.__wdio_original_internals_invoke__ = base;

  // Best-effort only. Under `devUrl` this property is writable and patching it
  // gives a second mock path. Under embed/production mode it is non-writable
  // and non-configurable, so assignment throws `TypeError: Attempted to assign
  // to readonly property` — which previously aborted the whole bootstrap module
  // before the reboot hook installed. App-level mocking now goes through the
  // `@tauri-apps/api/core` alias shim (see vite.config.ts), so failing here is
  // harmless: swallow it and let the bootstrap continue.
  try {
    internals.invoke = async (cmd, args, options) => {
      const mockFn = w.__wdio_mocks__?.[cmd];
      if (mockFn && typeof mockFn === "function") {
        return await mockFn(args);
      }
      return base(cmd, args, options);
    };
    internals._wdioInvokePatched = true;
  } catch {
    document.documentElement.dataset.wdioInvokeLocked = "1";
  }
  return true;
}

function startInternalsPatchLoop(): void {
  if (typeof window === "undefined") {
    return;
  }
  let attempts = 0;
  const maxAttempts = 100;
  const tick = (): void => {
    attempts += 1;
    if (patchInternalsInvoke()) {
      return;
    }
    if (attempts < maxAttempts) {
      window.setTimeout(tick, 50);
    }
  };
  tick();
}

startInternalsPatchLoop();
document.documentElement.dataset.bsAfterPatch = "1";

type RebootWindow = Window & { __lumogis_overlay_reboot?: () => Promise<void> };

let rebootRunner: (() => Promise<void>) | null = null;

/** Register before `./app` loads — main.e2e wires the real boot implementation. */
function installRebootHookStub(): void {
  const w = window as RebootWindow;
  w.__lumogis_overlay_reboot = async () => {
    if (!rebootRunner) {
      throw new Error("overlay boot runner not registered");
    }
    await rebootRunner();
  };
  document.documentElement.dataset.lumogisReboot = "1";
}

document.documentElement.dataset.bsPreStub = "1";
installRebootHookStub();
document.documentElement.dataset.bsPostStub = "1";

export function setRebootRunner(fn: () => Promise<void>): void {
  rebootRunner = fn;
}
