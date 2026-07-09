// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/**
 * @vitest-environment happy-dom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OverlayAppContext } from "./app";
import {
  SUMMON_HINT_DISMISS_MS,
  SUMMON_HINT_STORAGE_KEY,
  type SummonRecoveryState,
  activateSummonHint,
  dismissSummonHint,
  isRecoveryHintActive,
  isSummonHintSeen,
  markSummonHintSeen,
  offerRecoveryHintIfNeeded,
  optOutRecoveryHint,
  recoveryHintMarkup,
  resetRecoveryHintStateForTests,
  resetSummonHintStateForTests,
  scheduleSummonHintDismiss,
  shouldShowRecoveryHint,
  summonHintMarkup,
} from "./summonHint";

function makeCtx(over: Partial<OverlayAppContext> = {}): OverlayAppContext {
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  let renderCount = 0;
  return {
    profile: "client-only",
    settings: {
      schemaVersion: 2,
      orchestratorBaseUrl: "http://127.0.0.1:8000",
      hotkey: "CommandOrControl+Shift+L",
      libraryRoots: [],
      theme: "system",
      onboardingComplete: true,
    },
    root,
    render() {
      renderCount += 1;
      if (over.render) {
        over.render();
      }
    },
    async refreshSettings() {},
    async setLibraryRoots() {},
    async invoke() {
      return false as never;
    },
    async listen() {},
    ...over,
  } as OverlayAppContext;
}

beforeEach(() => {
  localStorage.clear();
  resetSummonHintStateForTests();
  resetRecoveryHintStateForTests();
  document.body.innerHTML = "";
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  resetSummonHintStateForTests();
  resetRecoveryHintStateForTests();
  localStorage.clear();
});

describe("summonHint storage", () => {
  it("markSummonHintSeen sets storage key", () => {
    expect(isSummonHintSeen()).toBe(false);
    markSummonHintSeen();
    expect(localStorage.getItem(SUMMON_HINT_STORAGE_KEY)).toBe("1");
    expect(isSummonHintSeen()).toBe(true);
  });
});

describe("summonHintMarkup", () => {
  it("summon_hint_copy_exact_template", () => {
    const el = summonHintMarkup("Ctrl+Shift+L");
    expect(el.textContent).toContain("Press ");
    expect(el.textContent).toContain("Ctrl+Shift+L");
    expect(el.textContent).toContain("anytime to open search");
    expect(el.querySelector("strong")?.textContent).toBe("Ctrl+Shift+L");
  });
});

describe("activateSummonHint", () => {
  it("activateSummonHint_idempotent_when_active", () => {
    const renders: number[] = [];
    const ctx = makeCtx({
      render() {
        renders.push(Date.now());
      },
    });
    activateSummonHint(ctx);
    activateSummonHint(ctx);
    expect(renders).toHaveLength(1);
  });

  it("summon_hint_mark_seen_on_timeout", () => {
    const ctx = makeCtx();
    activateSummonHint(ctx);
    expect(isSummonHintSeen()).toBe(false);
    vi.advanceTimersByTime(SUMMON_HINT_DISMISS_MS);
    expect(isSummonHintSeen()).toBe(true);
  });

  it("scheduleSummonHintDismiss fires once", () => {
    const cb = vi.fn();
    scheduleSummonHintDismiss(cb);
    scheduleSummonHintDismiss(cb);
    vi.advanceTimersByTime(SUMMON_HINT_DISMISS_MS);
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("dismissSummonHint clears active state", () => {
    const ctx = makeCtx();
    activateSummonHint(ctx);
    dismissSummonHint(ctx);
    expect(isSummonHintSeen()).toBe(true);
  });
});

describe("LUM-455 Wayland recovery hint", () => {
  const state = (over: Partial<SummonRecoveryState> = {}): SummonRecoveryState => ({
    wayland: true,
    desktop: "gnome",
    recoveryConfirmed: false,
    showOnceOptOut: false,
    ...over,
  });

  it("shouldShowRecoveryHint only on unconfirmed, non-opted-out Wayland", () => {
    expect(shouldShowRecoveryHint(state())).toBe(true);
    expect(shouldShowRecoveryHint(state({ recoveryConfirmed: true }))).toBe(false);
    expect(shouldShowRecoveryHint(state({ showOnceOptOut: true }))).toBe(false);
    expect(shouldShowRecoveryHint(state({ wayland: false }))).toBe(false);
  });

  it("GNOME copy is keybinding-first and includes the --toggle command", () => {
    const el = recoveryHintMarkup(state({ desktop: "gnome" }), () => {});
    expect(el.textContent).toContain("keyboard shortcut");
    expect(el.querySelector(".summon-recovery-hint__cmd")?.textContent).toBe("lumogis-search --toggle");
    expect(el.textContent).not.toContain("tray icon");
  });

  it("KDE copy offers the tray icon", () => {
    const el = recoveryHintMarkup(state({ desktop: "kde" }), () => {});
    expect(el.textContent).toContain("tray icon");
    expect(el.querySelector(".summon-recovery-hint__cmd")?.textContent).toBe("lumogis-search --toggle");
  });

  it("offerRecoveryHintIfNeeded activates + renders on unconfirmed Wayland", async () => {
    let renders = 0;
    const ctx = makeCtx({
      render() {
        renders += 1;
      },
      async invoke<T>() {
        return state() as unknown as T;
      },
    });
    await offerRecoveryHintIfNeeded(ctx);
    expect(isRecoveryHintActive()).toBe(true);
    expect(renders).toBe(1);
  });

  it("does NOT activate when recovery already confirmed", async () => {
    const ctx = makeCtx({
      async invoke<T>() {
        return state({ recoveryConfirmed: true }) as unknown as T;
      },
    });
    await offerRecoveryHintIfNeeded(ctx);
    expect(isRecoveryHintActive()).toBe(false);
  });

  it("divergence: ignores the X11 localStorage seen-flag (still activates)", async () => {
    // The X11 hotkey-hint flag must NOT suppress the Wayland recovery hint.
    markSummonHintSeen();
    expect(isSummonHintSeen()).toBe(true);
    const ctx = makeCtx({
      async invoke<T>() {
        return state() as unknown as T;
      },
    });
    await offerRecoveryHintIfNeeded(ctx);
    expect(isRecoveryHintActive()).toBe(true);
  });

  it("optOutRecoveryHint persists via set_show_once_opt_out and deactivates (active→inactive)", async () => {
    const calls: string[] = [];
    const ctx = makeCtx({
      async invoke<T>(cmd: string) {
        calls.push(cmd);
        // get_summon_recovery_state → an unconfirmed Wayland state so the hint activates;
        // set_show_once_opt_out → void.
        return (cmd === "get_summon_recovery_state" ? state() : undefined) as unknown as T;
      },
    });
    await offerRecoveryHintIfNeeded(ctx);
    expect(isRecoveryHintActive()).toBe(true); // genuinely active before opt-out
    await optOutRecoveryHint(ctx);
    expect(calls).toContain("set_show_once_opt_out");
    expect(isRecoveryHintActive()).toBe(false);
  });
});
