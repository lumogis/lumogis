// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-993: the shortcuts overlay is the shared Dialog.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ShortcutsHelp } from "../../../src/features/email/ShortcutsHelp";
import { SHORTCUTS } from "../../../src/features/email/useTriageKeyboard";

describe("ShortcutsHelp", () => {
  it("renders nothing while closed", () => {
    render(<ShortcutsHelp open={false} onClose={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists every bound shortcut in a labelled dialog", () => {
    render(<ShortcutsHelp open onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: /keyboard shortcuts/i });
    for (const s of SHORTCUTS) {
      expect(dialog.textContent).toContain(s.keys);
      expect(dialog.textContent).toContain(s.label);
    }
  });

  it("closes on Escape and from the close button", async () => {
    const onClose = vi.fn();
    render(<ShortcutsHelp open onClose={onClose} />);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
