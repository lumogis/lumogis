// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

const DEFAULT_HOTKEY = "CommandOrControl+Shift+L";

function detectPlatform(): "macos" | "other" {
  if (typeof navigator === "undefined") {
    return "other";
  }
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const platform = nav.userAgentData?.platform ?? navigator.platform ?? "";
  return /mac/i.test(platform) ? "macos" : "other";
}

function formatToken(token: string, isMac: boolean): string {
  const lower = token.toLowerCase();
  if (lower === "commandorcontrol" || lower === "command") {
    return isMac ? "⌘" : "Ctrl";
  }
  if (lower === "control" || lower === "ctrl") {
    return isMac ? "⌃" : "Ctrl";
  }
  if (lower === "shift") {
    return isMac ? "⇧" : "Shift";
  }
  if (lower === "alt" || lower === "option") {
    return isMac ? "⌥" : "Alt";
  }
  return token.length === 1 ? token.toUpperCase() : token;
}

/** Platform-aware hotkey label for overlay copy (LUM-456). */
export function formatHotkeyForDisplay(hotkey: string): string {
  const raw = hotkey.trim() || DEFAULT_HOTKEY;
  const isMac = detectPlatform() === "macos";
  const parts = raw.split("+").map((p) => p.trim()).filter(Boolean);
  const formatted = parts.map((part) => formatToken(part, isMac));
  return isMac ? formatted.join("") : formatted.join("+");
}
