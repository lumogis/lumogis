// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/**
 * Overlay application parity tests (LUM-435 Chunk B).
 *
 * Boots the shared `createOverlayApp()` factory in happy-dom with mocked Tauri
 * APIs and asserts the rendered markup and event-driven behaviour match the
 * pre-refactor overlay. Chunk B1 (pure move) is covered by the markup-parity
 * cases; Chunk B2 (hook seam) extends this with the event-driven cases
 * (corrupt-config toast, hotkey-failure banner, settings-saved re-render).
 *
 * @vitest-environment happy-dom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type InvokeArgs = Record<string, unknown> | undefined;
const invokeMock = vi.fn(async (_cmd: string, _args?: InvokeArgs): Promise<unknown> => undefined);
const listenMock =
  vi.fn(async (_event: string, _cb: (ev: { payload: unknown }) => void) => () => {});
const windowCloseMock = vi.fn();
const windowOnFocusChangedMock = vi.fn(
  async (_handler: (ev: { payload: boolean }) => void) => () => {},
);

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (cmd: string, args?: InvokeArgs) => invokeMock(cmd, args),
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: (event: string, cb: (ev: { payload: unknown }) => void) => listenMock(event, cb),
}));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({
    close: windowCloseMock,
    onFocusChanged: windowOnFocusChangedMock,
  }),
}));

// Imported after the mocks are registered (vi.mock is hoisted).
import { createOverlayApp, type OverlayAppContext } from "./app";
import { SUMMON_HINT_STORAGE_KEY, resetSummonHintStateForTests } from "./summonHint";

type OverlaySettings = {
  schemaVersion: number;
  orchestratorBaseUrl: string;
  hotkey: string;
  libraryRoots: string[];
  theme: string;
  onboardingComplete?: boolean;
  keychainError?: string | null;
};

type AuthProbe = { mode: string; sessionPresent: boolean; role?: string | null };

function makeSettings(over: Partial<OverlaySettings> = {}): OverlaySettings {
  return {
    schemaVersion: 2,
    orchestratorBaseUrl: "http://127.0.0.1:8000",
    hotkey: "CommandOrControl+Shift+L",
    libraryRoots: [],
    theme: "system",
    onboardingComplete: true,
    keychainError: null,
    ...over,
  };
}

/** Drives the Rust command surface boot() depends on. */
function wireInvoke(settings: OverlaySettings, probe: AuthProbe) {
  invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
    switch (cmd) {
      case "get_desktop_profile":
        return "client-only";
      case "get_overlay_settings":
        return settings;
      case "probe_auth_state":
        return probe;
      case "fetch_admin_settings":
        return {
          ingestPaths: [],
          pendingIngestPaths: null,
          restartRequired: false,
          paperlessConfigured: false,
        };
      case "take_pending_summon_hint":
        return false;
      default:
        return undefined;
    }
  });
}

function stubMatchMedia() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

/** Returns the callback the app registered for a given lifecycle event. */
function listenerFor(event: string): (ev: { payload: unknown }) => void {
  const call = listenMock.mock.calls.find((c) => c[0] === event);
  if (!call) throw new Error(`no listener registered for ${event}`);
  return call[1] as (ev: { payload: unknown }) => void;
}

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  invokeMock.mockReset();
  listenMock.mockClear();
  windowCloseMock.mockClear();
  windowOnFocusChangedMock.mockClear();
  localStorage.clear();
  resetSummonHintStateForTests();
  stubMatchMedia();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("createOverlayApp markup parity (Chunk B1)", () => {
  it("boots to the main search shell when onboarding is complete", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    await createOverlayApp().boot();

    const root = document.querySelector<HTMLDivElement>("#root")!;
    expect(root.querySelector("#onboarding")).toBeNull();
    const q = root.querySelector<HTMLInputElement>("#q");
    expect(q).not.toBeNull();
    expect(q!.disabled).toBe(false);
    expect(root.querySelector("#btn-settings")).not.toBeNull();
    expect(root.querySelector(".overlay-card")).not.toBeNull();
    expect(root.querySelector("#settings")).toBeNull();
    expect(root.querySelector("#results")).not.toBeNull();
  });

  it("registers the three lifecycle listeners on boot", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    await createOverlayApp().boot();

    expect(listenMock).toHaveBeenCalledWith("overlay-config-corrupt", expect.any(Function));
    expect(listenMock).toHaveBeenCalledWith("hotkey-register-failed", expect.any(Function));
    expect(listenMock).toHaveBeenCalledWith("settings-saved", expect.any(Function));
  });

  it("boots to the onboarding wizard when onboarding is incomplete", async () => {
    wireInvoke(makeSettings({ onboardingComplete: false }), {
      mode: "off",
      sessionPresent: false,
    });
    await createOverlayApp().boot();

    const root = document.querySelector<HTMLDivElement>("#root")!;
    expect(root.querySelector("#onboarding")).not.toBeNull();
    expect(root.querySelector("#q")).toBeNull();
  });

  it("disables the search box when auth is required but no session is present", async () => {
    wireInvoke(makeSettings(), { mode: "on", sessionPresent: false });
    await createOverlayApp().boot();

    const root = document.querySelector<HTMLDivElement>("#root")!;
    const q = root.querySelector<HTMLInputElement>("#q");
    expect(q).not.toBeNull();
    expect(q!.disabled).toBe(true);
    expect(root.querySelector("#login-panel")).not.toBeNull();
  });
});

describe("createOverlayApp event-driven behaviour (Chunk B2)", () => {
  it("offers reset on the overlay-config-corrupt event and reloads on confirm", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    const confirmMock = vi.fn((_message?: string) => true);
    vi.stubGlobal("confirm", confirmMock);
    const reloadSpy = vi.spyOn(window.location, "reload").mockImplementation(() => {});
    await createOverlayApp().boot();

    invokeMock.mockClear();
    listenerFor("overlay-config-corrupt")({
      payload: { error: "unsupported_schema_version:9", path: "/tmp/overlay.json" },
    });
    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(confirmMock.mock.calls[0][0]).toContain("overlay.json is corrupt");
    await vi.waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith("reset_overlay_config_to_defaults", undefined);
      expect(reloadSpy).toHaveBeenCalledTimes(1);
    });
  });

  it("closes the window when the corrupt-config reset is declined", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    vi.stubGlobal("confirm", vi.fn(() => false));
    await createOverlayApp().boot();

    listenerFor("overlay-config-corrupt")({ payload: { error: "bad", path: "/tmp/x" } });
    expect(windowCloseMock).toHaveBeenCalledTimes(1);
  });

  it("alerts on the hotkey-register-failed event", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    const alertMock = vi.fn();
    vi.stubGlobal("alert", alertMock);
    await createOverlayApp().boot();

    listenerFor("hotkey-register-failed")({ payload: "invalid_hotkey:Foo+Bar" });
    expect(alertMock).toHaveBeenCalledTimes(1);
    expect(String(alertMock.mock.calls[0][0])).toContain("Global hotkey registration failed");
  });

  it("re-reads settings from Rust on the settings-saved event", async () => {
    const settings = makeSettings();
    wireInvoke(settings, { mode: "off", sessionPresent: false });
    await createOverlayApp().boot();

    const before = invokeMock.mock.calls.filter((c) => c[0] === "get_overlay_settings").length;
    await listenerFor("settings-saved")({ payload: null });
    const after = invokeMock.mock.calls.filter((c) => c[0] === "get_overlay_settings").length;
    expect(after).toBe(before + 1);
  });
});

describe("createOverlayApp hook seam (Chunk B2)", () => {
  it("default resolveProfile invokes get_desktop_profile before the first render", async () => {
    wireInvoke(makeSettings({ onboardingComplete: false }), {
      mode: "off",
      sessionPresent: false,
    });
    let profileAtRender: string | undefined;
    const renderOnboarding = vi.fn((ctx: OverlayAppContext) => {
      profileAtRender = ctx.profile;
      return false;
    });
    await createOverlayApp({ renderOnboarding }).boot();

    expect(invokeMock).toHaveBeenCalledWith("get_desktop_profile", undefined);
    expect(renderOnboarding).toHaveBeenCalled();
    // Hook ran during render(), and profile was already resolved by then.
    expect(profileAtRender).toBe("client-only");
  });

  it("custom resolveProfile result is exposed on ctx", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    let bootedProfile: string | undefined;
    await createOverlayApp({
      resolveProfile: async () => "bundled",
      onBoot: async (ctx) => {
        bootedProfile = ctx.profile;
      },
    }).boot();
    expect(bootedProfile).toBe("bundled");
  });

  it("renderOnboarding returning true suppresses the shared wizard", async () => {
    wireInvoke(makeSettings({ onboardingComplete: false }), {
      mode: "off",
      sessionPresent: false,
    });
    const renderOnboarding = vi.fn(() => true);
    await createOverlayApp({ renderOnboarding }).boot();

    const root = document.querySelector<HTMLDivElement>("#root")!;
    expect(renderOnboarding).toHaveBeenCalled();
    expect(root.querySelector("#onboarding")).toBeNull();
    expect(root.innerHTML).toBe("");
  });

  it("renderOnboarding returning false falls back to the shared wizard", async () => {
    wireInvoke(makeSettings({ onboardingComplete: false }), {
      mode: "off",
      sessionPresent: false,
    });
    await createOverlayApp({ renderOnboarding: () => false }).boot();
    expect(document.querySelector("#onboarding")).not.toBeNull();
  });

  it("computeSearchDisabled override disables the search box", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    await createOverlayApp({ computeSearchDisabled: () => true }).boot();

    const q = document.querySelector<HTMLInputElement>("#q");
    expect(q).not.toBeNull();
    expect(q!.disabled).toBe(true);
  });

  it("overlayChrome renders inside the search shell when provided", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    await createOverlayApp({
      overlayChrome: () => '<div class="hub-tray" id="hub-tray">Hub — running</div>',
    }).boot();
    expect(document.querySelector("#hub-tray")).not.toBeNull();
    expect(document.querySelector(".overlay-card")).not.toBeNull();
  });

  it("startingBanner renders atop the main shell when provided, absent otherwise", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    await createOverlayApp({ startingBanner: () => "Starting Lumogis…" }).boot();
    const banner = document.querySelector("#starting-banner");
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain("Starting Lumogis…");

    document.body.innerHTML = '<div id="root"></div>';
    await createOverlayApp().boot();
    expect(document.querySelector("#starting-banner")).toBeNull();
  });

  it("onBoot runs after the initial render with a usable ctx", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    let rootHadSearchInput = false;
    await createOverlayApp({
      onBoot: async (ctx) => {
        rootHadSearchInput = ctx.root.querySelector("#q") !== null;
      },
    }).boot();
    expect(rootHadSearchInput).toBe(true);
  });
});

describe("createOverlayApp summon hint (LUM-456)", () => {
  it("registers onFocusChanged on boot", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    await createOverlayApp().boot();
    expect(windowOnFocusChangedMock).toHaveBeenCalledTimes(1);
  });

  it("take_pending_summon_hint_offers_on_boot", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
      if (cmd === "take_pending_summon_hint") return true;
      if (cmd === "get_desktop_profile") return "client-only";
      if (cmd === "get_overlay_settings") return makeSettings();
      if (cmd === "probe_auth_state") return { mode: "off", sessionPresent: false };
      return undefined;
    });
    await createOverlayApp().boot();
    const hint = document.querySelector("#summon-hint");
    expect(hint).not.toBeNull();
    expect(hint!.textContent).toContain("anytime to open search");
  });

  it("summon_hint_suppressed_when_localStorage_seen", async () => {
    localStorage.setItem(SUMMON_HINT_STORAGE_KEY, "1");
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
      if (cmd === "take_pending_summon_hint") return true;
      if (cmd === "get_desktop_profile") return "client-only";
      if (cmd === "get_overlay_settings") return makeSettings();
      if (cmd === "probe_auth_state") return { mode: "off", sessionPresent: false };
      return undefined;
    });
    await createOverlayApp().boot();
    expect(document.querySelector("#summon-hint")).toBeNull();
  });

  it("summon_hint_shown_after_onboarding_when_not_seen", async () => {
    wireInvoke(makeSettings({ onboardingComplete: false }), {
      mode: "off",
      sessionPresent: false,
    });
    invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
      if (cmd === "probe_server_health") return { status: "ok", message: null };
      if (cmd === "complete_onboarding") return undefined;
      if (cmd === "get_desktop_profile") return "client-only";
      if (cmd === "get_overlay_settings") {
        return makeSettings({ onboardingComplete: false });
      }
      if (cmd === "probe_auth_state") return { mode: "off", sessionPresent: false };
      if (cmd === "take_pending_summon_hint") return false;
      return undefined;
    });
    await createOverlayApp().boot();
    const base = document.querySelector<HTMLInputElement>("#onboard-base");
    expect(base).not.toBeNull();
    base!.value = "http://127.0.0.1:8000";
    document.querySelector<HTMLButtonElement>("#btn-test-connection")!.click();
    await vi.waitFor(() => {
      const btn = document.querySelector<HTMLButtonElement>("#btn-onboard-continue");
      expect(btn).not.toBeNull();
      expect(btn!.disabled).toBe(false);
    });
    invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
      if (cmd === "complete_onboarding") return undefined;
      if (cmd === "get_desktop_profile") return "client-only";
      if (cmd === "get_overlay_settings") {
        return makeSettings({ onboardingComplete: true });
      }
      if (cmd === "probe_auth_state") return { mode: "off", sessionPresent: false };
      if (cmd === "take_pending_summon_hint") return false;
      return undefined;
    });
    document.querySelector<HTMLButtonElement>("#btn-onboard-continue")!.click();
    await vi.waitFor(() => {
      expect(document.querySelector("#summon-hint")).not.toBeNull();
    });
    expect(document.querySelector("#summon-hint")!.textContent).toContain("Shift");
  });

  it("summon_hint_not_shown_during_onboarding", async () => {
    wireInvoke(makeSettings({ onboardingComplete: false }), {
      mode: "off",
      sessionPresent: false,
    });
    await createOverlayApp().boot();
    expect(document.querySelector("#summon-hint")).toBeNull();
  });

  it("summon_hint_does_not_disable_search_input", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
      if (cmd === "take_pending_summon_hint") return true;
      if (cmd === "get_desktop_profile") return "client-only";
      if (cmd === "get_overlay_settings") return makeSettings();
      if (cmd === "probe_auth_state") return { mode: "off", sessionPresent: false };
      return undefined;
    });
    await createOverlayApp().boot();
    const q = document.querySelector<HTMLInputElement>("#q");
    expect(q).not.toBeNull();
    expect(q!.disabled).toBe(false);
    expect(document.activeElement?.id).not.toBe("summon-hint");
  });

  it("render_does_not_duplicate_active_hint", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
      if (cmd === "take_pending_summon_hint") return true;
      if (cmd === "get_desktop_profile") return "client-only";
      if (cmd === "get_overlay_settings") return makeSettings();
      if (cmd === "probe_auth_state") return { mode: "off", sessionPresent: false };
      return undefined;
    });
    await createOverlayApp({
      onBoot: async (ctx) => {
        ctx.render();
      },
    }).boot();
    expect(document.querySelectorAll("#summon-hint").length).toBe(1);
  });

  it("summon_hint_copy_exact_template", async () => {
    wireInvoke(makeSettings(), { mode: "off", sessionPresent: false });
    invokeMock.mockImplementation(async (cmd: string): Promise<unknown> => {
      if (cmd === "take_pending_summon_hint") return true;
      if (cmd === "get_desktop_profile") return "client-only";
      if (cmd === "get_overlay_settings") return makeSettings();
      if (cmd === "probe_auth_state") return { mode: "off", sessionPresent: false };
      return undefined;
    });
    await createOverlayApp().boot();
    expect(document.querySelector("#summon-hint")!.textContent).toContain("anytime to open search");
  });
});
