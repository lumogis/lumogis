// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { formatHotkeyForDisplay } from "./hotkeyDisplay";
import type { OverlayAppContext } from "./app";

export const SUMMON_HINT_STORAGE_KEY = "lumogis.overlay.summonHintSeen";
export const SUMMON_HINT_DISMISS_MS = 7000;

let summonHintActive = false;
let dismissTimer: ReturnType<typeof setTimeout> | null = null;

export function isSummonHintActive(): boolean {
  return summonHintActive;
}

/** Test-only reset for Vitest isolation. */
export function resetSummonHintStateForTests(): void {
  summonHintActive = false;
  if (dismissTimer !== null) {
    clearTimeout(dismissTimer);
    dismissTimer = null;
  }
}

export function isSummonHintSeen(): boolean {
  try {
    return localStorage.getItem(SUMMON_HINT_STORAGE_KEY) === "1";
  } catch {
    return true;
  }
}

export function markSummonHintSeen(): void {
  try {
    localStorage.setItem(SUMMON_HINT_STORAGE_KEY, "1");
  } catch {
    // fail silent — treat as seen
  }
}

export function shouldOfferSummonHint(): boolean {
  return !isSummonHintSeen();
}

export function summonHintMarkup(hotkeyDisplay: string): HTMLElement {
  const el = document.createElement("p");
  el.id = "summon-hint";
  el.className = "summon-hint";
  el.appendChild(document.createTextNode("Press "));
  const strong = document.createElement("strong");
  strong.textContent = hotkeyDisplay;
  el.appendChild(strong);
  el.appendChild(document.createTextNode(" anytime to open search"));
  return el;
}

export function scheduleSummonHintDismiss(onDismiss: () => void): void {
  if (dismissTimer !== null) {
    clearTimeout(dismissTimer);
  }
  dismissTimer = setTimeout(() => {
    dismissTimer = null;
    onDismiss();
  }, SUMMON_HINT_DISMISS_MS);
}

export function upsertSummonHintElement(root: HTMLElement, hotkey: string): void {
  if (root.querySelector("#summon-hint")) {
    return;
  }
  const display = formatHotkeyForDisplay(hotkey.trim() || "CommandOrControl+Shift+L");
  root.appendChild(summonHintMarkup(display));
}

export function dismissSummonHint(ctx: Pick<OverlayAppContext, "render">): void {
  if (!summonHintActive) {
    return;
  }
  if (dismissTimer !== null) {
    clearTimeout(dismissTimer);
    dismissTimer = null;
  }
  summonHintActive = false;
  markSummonHintSeen();
  ctx.render();
}

export function activateSummonHint(ctx: OverlayAppContext): void {
  if (summonHintActive || !shouldOfferSummonHint()) {
    return;
  }
  summonHintActive = true;
  ctx.render();
  scheduleSummonHintDismiss(() => dismissSummonHint(ctx));
}

export async function offerSummonHintIfPending(ctx: OverlayAppContext): Promise<void> {
  if (!shouldOfferSummonHint() || summonHintActive) {
    return;
  }
  try {
    const pending = await ctx.invoke<boolean>("take_pending_summon_hint");
    if (pending) {
      activateSummonHint(ctx);
    }
  } catch {
    // fail silent
  }
}
