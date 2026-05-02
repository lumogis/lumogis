// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Theme controller (Phase 1 Pass 1.1 item 6).
//
// - Default: "system" (no `data-theme` on <html>; prefers-color-scheme wins).
// - Override: "light" or "dark" (sets data-theme on <html>; persists to
//   localStorage so the choice survives reloads).
// - Public: getStoredTheme(), setStoredTheme(), applyTheme(), initTheme().
// - Pure helper: getContrastRatio(fg, bg) — WCAG-AA computation, used by
//   tests/design/contrast.test.ts to lock the token palette against
//   accessibility regressions.

export type ThemeMode = "system" | "light" | "dark";

const STORAGE_KEY = "lumogis-theme";

export function getStoredTheme(): ThemeMode {
  if (typeof localStorage === "undefined") return "system";
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

export function setStoredTheme(mode: ThemeMode): void {
  if (typeof localStorage !== "undefined") {
    if (mode === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, mode);
  }
  applyTheme(mode);
}

export function applyTheme(mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (mode === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", mode);
  }
}

export function initTheme(): void {
  applyTheme(getStoredTheme());
}

export function effectiveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode !== "system") return mode;
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// ---------------------------------------------------------------------------
// WCAG contrast helpers
// ---------------------------------------------------------------------------

function parseHex(input: string): [number, number, number] {
  const m = input.trim().replace(/^#/, "");
  if (m.length === 3) {
    const r = parseInt(m[0]! + m[0]!, 16);
    const g = parseInt(m[1]! + m[1]!, 16);
    const b = parseInt(m[2]! + m[2]!, 16);
    return [r, g, b];
  }
  if (m.length === 6) {
    return [
      parseInt(m.slice(0, 2), 16),
      parseInt(m.slice(2, 4), 16),
      parseInt(m.slice(4, 6), 16),
    ];
  }
  throw new Error(`getContrastRatio: invalid hex colour "${input}"`);
}

function channelToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function relativeLuminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map(channelToLinear) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * Compute the WCAG 2.1 contrast ratio between two sRGB hex colours. Returns
 * a number in the range [1, 21]. WCAG-AA requires ≥ 4.5 for body text and
 * ≥ 3.0 for large text + non-text UI components.
 */
export function getContrastRatio(fg: string, bg: string): number {
  const lFg = relativeLuminance(parseHex(fg));
  const lBg = relativeLuminance(parseHex(bg));
  const [lighter, darker] = lFg > lBg ? [lFg, lBg] : [lBg, lFg];
  return (lighter + 0.05) / (darker + 0.05);
}
