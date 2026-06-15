// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { $, browser } from "@wdio/globals";
import {
  loggedInProbe,
  loggedInSettings,
  loggedOutProbe,
  loggedOutSettings,
  type AdminSettingsPublic,
  type AuthProbeResult,
  type OverlaySettingsPayload,
} from "../mocks/invokeFixtures.js";
import { mockInvokeReturn } from "./mockInvoke.js";

export type BootOverlayOptions = {
  settings?: OverlaySettingsPayload;
  probe?: AuthProbeResult;
  adminSettings?: AdminSettingsPublic | null;
  /** When false, skip invoke mocks (smoke leg). */
  mockInvoke?: boolean;
};

const DEFAULT_ADMIN: AdminSettingsPublic = {
  ingestPaths: ["/data/ingest"],
  pendingIngestPaths: null,
  restartRequired: false,
  paperlessConfigured: false,
};

type RebootWindow = Window & {
  __lumogis_overlay_reboot?: () => Promise<void>;
};

type PluginProbe = {
  spy: boolean;
  invokeReady: boolean;
};

type RebootProbe = {
  reboot: boolean;
  dataset: boolean;
  href: string;
  script: string;
  rootLen: number;
  bootError: string;
};

/**
 * Read-only probes via string scripts. Arrow functions passed to `browser.tauri.execute`
 * wait up to 5s for `__wdio_original_core__.invoke` on every call (embedded direct-eval wrap).
 */
async function tauriPageEval<T>(script: string): Promise<T> {
  return browser.tauri.execute(script) as Promise<T>;
}

async function probePluginReady(): Promise<PluginProbe> {
  return tauriPageEval(`
    const w = window;
    return {
      spy: typeof w.__wdio_spy__ !== 'undefined',
      invokeReady:
        typeof w.__wdio_original_core__?.invoke === 'function' ||
        typeof w.__TAURI_INTERNALS__?.invoke === 'function',
    };
  `);
}

async function waitForTauriBridge(): Promise<void> {
  await browser.waitUntil(
    async () => {
      const ready = await tauriPageEval<boolean>(`
        const w = window;
        return (
          typeof w.__TAURI_INTERNALS__?.invoke === 'function' ||
          typeof w.__TAURI__?.core?.invoke === 'function'
        );
      `);
      return ready;
    },
    { timeout: 60_000, timeoutMsg: "Tauri IPC bridge not injected in embedded webview" },
  );
}

async function waitForTauriPlugin(): Promise<void> {
  await waitForTauriBridge();
  await browser.waitUntil(
    async () => {
      const probe = await probePluginReady();
      return probe.spy;
    },
    { timeout: 60_000, timeoutMsg: "@wdio/tauri-plugin not loaded in E2E bundle" },
  );
  const probe = await probePluginReady();
  if (!probe.invokeReady) {
    throw new Error("Tauri invoke mock infrastructure not ready after E2E bundle load");
  }
  await browser.tauri.execute(async () => {
    const w = window as Window & {
      wdioTauri?: { waitForInit?: () => Promise<void> };
      __wdio_original_core__?: { invoke?: unknown };
      __TAURI__?: { core?: { invoke?: unknown } };
      __TAURI_INTERNALS__?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> };
    };
    if (typeof w.wdioTauri?.waitForInit === "function") {
      await w.wdioTauri.waitForInit();
    }
    if (!w.__wdio_original_core__?.invoke && w.__TAURI__?.core?.invoke) {
      (w as Window & { __wdio_original_core__?: unknown }).__wdio_original_core__ = w.__TAURI__.core;
    }
    if (!w.__wdio_original_core__?.invoke && w.__TAURI_INTERNALS__?.invoke) {
      (w as Window & { __wdio_original_core__?: { invoke?: unknown } }).__wdio_original_core__ = {
        invoke: w.__TAURI_INTERNALS__.invoke.bind(w.__TAURI_INTERNALS__),
      };
    }
  });
  await browser.waitUntil(
    async () => {
      const probe = await probePluginReady();
      return probe.invokeReady;
    },
    { timeout: 30_000, timeoutMsg: "Tauri core.invoke not snapshotted for WDIO mocks" },
  );
}

async function waitForFrontendEntry(): Promise<void> {
  await browser.waitUntil(
    async () => {
      const href = await tauriPageEval<string>("return location.href");
      const ready = await tauriPageEval<boolean>("return document.readyState === 'complete'");
      return href.startsWith("tauri://") && ready;
    },
    { timeout: 60_000, timeoutMsg: "embedded frontend did not load at tauri://localhost" },
  );
}

async function showMainWindow(): Promise<void> {
  await browser.tauri.execute(({ core }) =>
    core.invoke("plugin:window|show", { label: "main" }),
  );
}

async function probeRebootHook(): Promise<RebootProbe> {
  return tauriPageEval(`
    const w = window;
    const script =
      document.querySelector('script[type="module"]')?.getAttribute('src') ??
      document.querySelector('script[src]')?.getAttribute('src') ??
      '';
    return {
      reboot: typeof w.__lumogis_overlay_reboot === 'function',
      dataset: document.documentElement.dataset.lumogisReboot === '1',
      href: location.href,
      script,
      rootLen: document.getElementById('root')?.innerHTML.length ?? 0,
      bootError: document.documentElement.dataset.lumogisBootError ?? '',
    };
  `);
}

async function waitForRebootHook(): Promise<void> {
  const deadline = Date.now() + 60_000;
  let lastDiag: RebootProbe | null = null;
  while (Date.now() < deadline) {
    lastDiag = await probeRebootHook();
    if (lastDiag.reboot || lastDiag.dataset) {
      return;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  if (lastDiag?.href.startsWith("tauri://")) {
    const consoleDump = await tauriPageEval<string>(`
      return Array.from(document.querySelectorAll('script')).map(s => s.outerHTML).join('\\n');
    `).catch(() => "");
    throw new Error(
      `E2E bundle did not execute on ${lastDiag.href} — last=${JSON.stringify(lastDiag)} scripts=${consoleDump}`,
    );
  }
  throw new Error(`E2E bundle did not execute — last=${JSON.stringify(lastDiag)}`);
}

async function rebootOverlayApp(): Promise<void> {
  await waitForRebootHook();
  await browser.tauri.execute(async () => {
    const reboot = (window as RebootWindow).__lumogis_overlay_reboot;
    if (typeof reboot !== "function") {
      throw new Error("__lumogis_overlay_reboot missing — build with VITE_WDIO_E2E=true");
    }
    await reboot();
  });
}

async function waitForShell(): Promise<void> {
  await browser.waitUntil(
    async () => {
      const login = await $("#login-panel").isExisting().catch(() => false);
      const q = await $("#q").isExisting().catch(() => false);
      const onboarding = await $("#onboarding").isExisting().catch(() => false);
      return login || q || onboarding;
    },
    { timeout: 60_000, timeoutMsg: "Overlay shell did not render" },
  );
}

async function registerBootMocks(
  settings: OverlaySettingsPayload,
  probe: AuthProbeResult,
  adminSettings: AdminSettingsPublic | null,
): Promise<void> {
  await mockInvokeReturn("get_desktop_profile", "client-only");
  await mockInvokeReturn("get_overlay_settings", settings);
  await mockInvokeReturn("probe_auth_state", probe);
  await mockInvokeReturn("take_pending_summon_hint", false);
  if (adminSettings) {
    await mockInvokeReturn("fetch_admin_settings", adminSettings);
  }
}

async function assertEmbedOrigin(): Promise<void> {
  const href = await tauriPageEval<string>("return location.href");
  if (/^https?:\/\/localhost(:\d+)?/.test(href)) {
    throw new Error(
      `STOP: embed-only build still loads devUrl — href=${href}. ` +
        `tauri.wdio-e2e.conf.json merge did not clear devUrl at compile time.`,
    );
  }
  if (!href.startsWith("tauri://")) {
    throw new Error(`STOP: expected tauri:// embed origin — href=${href}`);
  }
}

/** Register boot-time invoke mocks and wait for overlay shell. */
export async function bootOverlay(options: BootOverlayOptions = {}): Promise<void> {
  const mockInvoke = options.mockInvoke !== false;
  const settings = options.settings ?? loggedOutSettings();
  const probe =
    options.probe ??
    (settings.sessionPresent
      ? loggedInProbe(settings.sessionRole ?? "admin")
      : loggedOutProbe());
  const adminSettings = options.adminSettings === undefined ? DEFAULT_ADMIN : options.adminSettings;

  await waitForFrontendEntry();
  await assertEmbedOrigin();
  const bootProbe = await probeRebootHook();
  console.info("[bootOverlay] embed probe:", bootProbe);
  await waitForRebootHook();
  await waitForTauriPlugin();

  if (!mockInvoke) {
    await showMainWindow();
    await waitForShell();
    return;
  }

  await registerBootMocks(settings, probe, adminSettings);
  await rebootOverlayApp();
  await showMainWindow();
  await waitForShell();

  if (settings.authMode === "on" && !settings.sessionPresent) {
    await browser.waitUntil(async () => $("#login-panel").isExisting(), {
      timeout: 30_000,
      timeoutMsg: "login panel not found after mocked reboot",
    });
  }
}

export async function bootLoggedInAdmin(
  over: Partial<OverlaySettingsPayload> = {},
): Promise<void> {
  await bootOverlay({
    settings: loggedInSettings("admin", over),
    probe: loggedInProbe("admin"),
    adminSettings: DEFAULT_ADMIN,
  });
}

export async function bootLoggedInMember(
  over: Partial<OverlaySettingsPayload> = {},
): Promise<void> {
  await bootOverlay({
    settings: loggedInSettings("member", over),
    probe: loggedInProbe("member"),
    adminSettings: null,
  });
}
