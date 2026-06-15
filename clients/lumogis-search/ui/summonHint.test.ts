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
  activateSummonHint,
  dismissSummonHint,
  isSummonHintSeen,
  markSummonHintSeen,
  resetSummonHintStateForTests,
  scheduleSummonHintDismiss,
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
  document.body.innerHTML = "";
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  resetSummonHintStateForTests();
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
