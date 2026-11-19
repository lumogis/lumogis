// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The sidebar preference must never break the page when the browser refuses
// storage (Safari private mode, blocked site data): LUM-993.

import { afterEach, describe, expect, it, vi } from "vitest";

import { getSidebarCollapsed, setSidebarCollapsed } from "../../src/design/sidebarCollapse";

const throwingStorage = {
  getItem: () => {
    throw new DOMException("denied", "SecurityError");
  },
  setItem: () => {
    throw new DOMException("denied", "SecurityError");
  },
  removeItem: () => {
    throw new DOMException("denied", "SecurityError");
  },
};

describe("sidebarCollapse with storage that throws", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.removeAttribute("data-sidebar-collapsed");
  });

  it("reads as expanded instead of throwing", () => {
    vi.stubGlobal("localStorage", throwingStorage);
    expect(getSidebarCollapsed()).toBe(false);
  });

  it("still collapses the current page when the write is refused", () => {
    vi.stubGlobal("localStorage", throwingStorage);
    expect(() => setSidebarCollapsed(true)).not.toThrow();
    expect(document.documentElement.hasAttribute("data-sidebar-collapsed")).toBe(true);
  });

  it("round-trips when storage works", () => {
    setSidebarCollapsed(true);
    expect(getSidebarCollapsed()).toBe(true);
    setSidebarCollapsed(false);
    expect(getSidebarCollapsed()).toBe(false);
  });
});
