// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

export type ThemeMode = "system" | "light" | "dark";

export function normalizeTheme(theme: string): ThemeMode {
  if (theme === "light" || theme === "dark" || theme === "system") {
    return theme;
  }
  return "system";
}

export function applyTheme(mode: ThemeMode): void {
  document.documentElement.setAttribute("data-theme", mode);
}

export function watchSystemTheme(onChange: () => void): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = () => onChange();
  mq.addEventListener("change", handler);
  return () => mq.removeEventListener("change", handler);
}
