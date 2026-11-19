// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

const STORAGE_KEY = "lumogis-sidebar-collapsed";

// Storage can be missing, or throw on access (Safari private mode, blocked site
// data). A preference must never break the page, so every access is guarded and
// falls back to "expanded" (LUM-993).
export function getSidebarCollapsed(): boolean {
  try {
    if (typeof localStorage === "undefined") return false;
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setSidebarCollapsed(collapsed: boolean): void {
  try {
    if (typeof localStorage !== "undefined") {
      if (collapsed) localStorage.setItem(STORAGE_KEY, "1");
      else localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* the attribute below still applies for this page */
  }
  applySidebarCollapsed(collapsed);
}

export function applySidebarCollapsed(collapsed: boolean): void {
  if (typeof document === "undefined") return;
  document.documentElement.toggleAttribute("data-sidebar-collapsed", collapsed);
}

export function initSidebarCollapsed(): void {
  applySidebarCollapsed(getSidebarCollapsed());
}
