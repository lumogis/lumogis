// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RowActions } from "../../../src/components/page/RowActions";

describe("RowActions", () => {
  it("opens from the labelled trigger and runs the chosen item", async () => {
    const user = userEvent.setup();
    const a = vi.fn();
    const b = vi.fn();
    render(
      <RowActions
        label="More actions for Work mail"
        items={[
          { label: "Switch", onSelect: a },
          { label: "Remove", onSelect: b, destructive: true },
        ]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "More actions for Work mail" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    expect(b).toHaveBeenCalledTimes(1);
    expect(a).not.toHaveBeenCalled();
  });

  it("does not run a disabled item", async () => {
    const user = userEvent.setup();
    const a = vi.fn();
    render(<RowActions label="More" items={[{ label: "Switch", onSelect: a, disabled: true }]} />);
    await user.click(screen.getByRole("button", { name: "More" }));
    await user.click(await screen.findByRole("menuitem", { name: "Switch" }));
    expect(a).not.toHaveBeenCalled();
  });
});
