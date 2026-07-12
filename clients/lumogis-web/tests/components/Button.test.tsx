// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button, buttonClassName } from "../../src/components/Button";

describe("Button", () => {
  it("applies variant and size classes", () => {
    expect(buttonClassName("primary", "md")).toContain("lumogis-btn--primary");
    expect(buttonClassName("danger", "sm")).toContain("lumogis-btn--sm");
  });

  it("renders disabled with same variant classes", () => {
    render(
      <Button variant="secondary" disabled>
        Disabled
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "Disabled" });
    expect(btn).toBeDisabled();
    expect(btn.className).toContain("lumogis-btn--secondary");
  });
});
