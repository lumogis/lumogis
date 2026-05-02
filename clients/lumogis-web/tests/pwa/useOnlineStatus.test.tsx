// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

import { useOnlineStatus } from "../../src/pwa/useOnlineStatus";
import { onlineManager } from "@tanstack/react-query";

function Harness(): JSX.Element {
  const online = useOnlineStatus();
  return <span data-testid="net">{online ? "online" : "offline"}</span>;
}

describe("useOnlineStatus (Phase 3E)", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "onLine", { value: true, writable: true, configurable: true });
  });

  afterEach(() => {
    Object.defineProperty(navigator, "onLine", { value: true, writable: true, configurable: true });
    onlineManager.setOnline(true);
    vi.restoreAllMocks();
  });

  it("initializes from navigator.onLine when available", async () => {
    Object.defineProperty(navigator, "onLine", { value: false, writable: true, configurable: true });
    render(<Harness />);
    expect(screen.getByTestId("net")).toHaveTextContent("offline");
  });

  it("updates on offline event and syncs onlineManager", async () => {
    render(<Harness />);
    await act(async () => {
      Object.defineProperty(navigator, "onLine", { value: false, writable: true, configurable: true });
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByTestId("net")).toHaveTextContent("offline");
    expect(onlineManager.isOnline()).toBe(false);
  });

  it("updates on online event", async () => {
    render(<Harness />);
    await act(async () => {
      Object.defineProperty(navigator, "onLine", { value: false, writable: true, configurable: true });
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByTestId("net")).toHaveTextContent("offline");
    await act(async () => {
      Object.defineProperty(navigator, "onLine", { value: true, writable: true, configurable: true });
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.getByTestId("net")).toHaveTextContent("online");
    expect(onlineManager.isOnline()).toBe(true);
  });

  it("cleans up online/offline listeners on unmount", () => {
    const spyAdd = vi.spyOn(window, "addEventListener");
    const spyRm = vi.spyOn(window, "removeEventListener");
    const { unmount } = render(<Harness />);
    const onlineAdds = spyAdd.mock.calls.filter((c) => c[0] === "online").length;
    expect(onlineAdds).toBeGreaterThanOrEqual(1);
    unmount();
    const onlineRemoves = spyRm.mock.calls.filter((c) => c[0] === "online").length;
    expect(onlineRemoves).toBeGreaterThanOrEqual(onlineAdds);
    spyAdd.mockRestore();
    spyRm.mockRestore();
  });
});
