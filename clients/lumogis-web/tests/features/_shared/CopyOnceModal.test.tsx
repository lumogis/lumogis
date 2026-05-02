// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Phase 2D — CopyOnceModal focus + Escape (credential / admin copy flows).

import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CopyOnceModal } from "../../../src/features/_shared/CopyOnceModal";

function Harness(): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open modal
      </button>
      <CopyOnceModal
        open={open}
        title="Secret"
        plaintext="tok-test"
        onClose={() => setOpen(false)}
      />
    </div>
  );
}

describe("CopyOnceModal", () => {
  it("focuses Copy first, restores focus to trigger on Close", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: /open modal/i });
    await user.click(opener);
    const copyBtn = await screen.findByRole("button", { name: /copy to clipboard/i });
    await waitFor(() => {
      expect(document.activeElement).toBe(copyBtn);
    });

    await user.click(screen.getByRole("button", { name: /^close$/i }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(document.activeElement).toBe(opener);
  });

  it("closes on Escape and restores focus", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: /open modal/i });
    await user.click(opener);
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(document.activeElement).toBe(opener);
  });
});
