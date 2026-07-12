// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SegmentedControl } from "../../src/components/SegmentedControl";

describe("SegmentedControl", () => {
  it("calls onChange for enabled segments", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <SegmentedControl
        ariaLabel="Range"
        value="7d"
        options={[
          { value: "24h", label: "24h" },
          { value: "7d", label: "7d" },
        ]}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: "24h" }));
    expect(onChange).toHaveBeenCalledWith("24h");
  });

  it("marks selected segment with aria-pressed", () => {
    render(
      <SegmentedControl
        ariaLabel="Range"
        value="7d"
        options={[
          { value: "24h", label: "24h" },
          { value: "7d", label: "7d" },
        ]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "7d" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "24h" })).toHaveAttribute("aria-pressed", "false");
  });
});
