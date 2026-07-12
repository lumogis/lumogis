// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

const STORAGE_KEY = "lumogis-sidebar-collapsed";

export function getSidebarCollapsed(): boolean {
  if (typeof localStorage === "undefined") return false;
  return localStorage.getItem(STORAGE_KEY) === "1";
}

export function setSidebarCollapsed(collapsed: boolean): void {
  if (typeof localStorage !== "undefined") {
    if (collapsed) localStorage.setItem(STORAGE_KEY, "1");
    else localStorage.removeItem(STORAGE_KEY);
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
