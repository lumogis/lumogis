// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { afterEach, describe, expect, it, vi } from "vitest";
import { formatHotkeyForDisplay } from "./hotkeyDisplay";

function stubPlatform(platform: string) {
  vi.stubGlobal("navigator", {
    platform,
    userAgentData: { platform },
  });
}

describe("formatHotkeyForDisplay", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("formatHotkeyForDisplay_macos_uses_command_glyph", () => {
    stubPlatform("macOS");
    const out = formatHotkeyForDisplay("CommandOrControl+Shift+L");
    expect(out).toMatch(/[⌘⌃]/);
    expect(out).not.toContain("CommandOrControl");
    expect(out).toContain("L");
  });

  it("formatHotkeyForDisplay_linux_uses_ctrl", () => {
    stubPlatform("Linux");
    const out = formatHotkeyForDisplay("CommandOrControl+Shift+L");
    expect(out).toContain("Ctrl");
    expect(out).toContain("Shift");
    expect(out).toContain("L");
    expect(out).not.toContain("⌘");
  });
});
