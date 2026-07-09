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

// ── LUM-455: Wayland re-summon recovery hint ────────────────────────────────
//
// A SEPARATE hint from the X11 "press the hotkey" hint above. Gated purely on the
// authoritative Rust `recoveryConfirmed`/`showOnceOptOut` flags (via
// `get_summon_recovery_state`); deliberately does NOT use the localStorage
// `SUMMON_HINT_STORAGE_KEY` seen-flag (that stays scoped to the X11 hint), so the
// two cannot diverge into a "cold-start-visible but no hint" state.

export interface SummonRecoveryState {
  wayland: boolean;
  desktop: "gnome" | "kde" | "other";
  recoveryConfirmed: boolean;
  showOnceOptOut: boolean;
}

let recoveryHintActive = false;
let recoveryState: SummonRecoveryState | null = null;

export function isRecoveryHintActive(): boolean {
  return recoveryHintActive;
}

/** Test-only reset for Vitest isolation. */
export function resetRecoveryHintStateForTests(): void {
  recoveryHintActive = false;
  recoveryState = null;
}

/** Show the Wayland recovery hint only on Wayland, while recovery is unconfirmed and
 * the user has not opted out. Rust flags are authoritative (no localStorage). */
export function shouldShowRecoveryHint(s: SummonRecoveryState): boolean {
  return s.wayland && !s.recoveryConfirmed && !s.showOnceOptOut;
}

/** DE-tailored copy: GNOME → keybinding-first (availability; tray may be absent);
 * KDE → tray + key (the tray is the focus-guaranteed path there). */
export function recoveryHintMarkup(
  state: SummonRecoveryState,
  onOptOut: () => void,
): HTMLElement {
  const el = document.createElement("div");
  el.id = "summon-recovery-hint";
  el.className = "summon-recovery-hint";

  const msg = document.createElement("p");
  msg.className = "summon-recovery-hint__msg";
  msg.textContent =
    state.desktop === "kde"
      ? "On Wayland the global shortcut may not work. Click the Lumogis tray icon to open search — or bind a keyboard shortcut to this command:"
      : "On Wayland the global shortcut may not work. Bind a keyboard shortcut (Settings → Keyboard) to this command to open search anytime:";
  el.appendChild(msg);

  const cmd = document.createElement("code");
  cmd.className = "summon-recovery-hint__cmd";
  cmd.textContent = "lumogis-search --toggle";
  el.appendChild(cmd);

  const optOut = document.createElement("button");
  optOut.type = "button";
  optOut.className = "summon-recovery-hint__optout";
  optOut.textContent = "Don't show on startup";
  optOut.addEventListener("click", onOptOut);
  el.appendChild(optOut);

  return el;
}

export function upsertRecoveryHintElement(root: HTMLElement, ctx: OverlayAppContext): void {
  if (!recoveryState || root.querySelector("#summon-recovery-hint")) {
    return;
  }
  root.appendChild(
    recoveryHintMarkup(recoveryState, () => {
      void optOutRecoveryHint(ctx);
    }),
  );
}

/** Persist the opt-out (Rust) and hide the hint — the only action that retires
 * show-once + the hint across launches. */
export async function optOutRecoveryHint(ctx: OverlayAppContext): Promise<void> {
  recoveryHintActive = false;
  recoveryState = null;
  try {
    await ctx.invoke("set_show_once_opt_out");
  } catch {
    // fail silent — keep the in-memory hide for this session
  }
  ctx.render();
}

/** On boot, ask Rust for the recovery state and activate the hint if needed. */
export async function offerRecoveryHintIfNeeded(ctx: OverlayAppContext): Promise<void> {
  if (recoveryHintActive) {
    return;
  }
  try {
    const s = await ctx.invoke<SummonRecoveryState>("get_summon_recovery_state");
    if (shouldShowRecoveryHint(s)) {
      recoveryState = s;
      recoveryHintActive = true;
      ctx.render();
    }
  } catch {
    // fail silent
  }
}
