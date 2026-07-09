// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IngestProgressBar } from "../../../src/features/documents/IngestProgressBar";

describe("IngestProgressBar", () => {
  it("renders progressbar with aria attributes", () => {
    render(<IngestProgressBar stage="embedding" progressPct={60} />);
    const bar = screen.getByRole("progressbar", { name: "Embedding" });
    expect(bar.getAttribute("aria-valuenow")).toBe("60");
    expect(bar.getAttribute("aria-valuemin")).toBe("0");
    expect(bar.getAttribute("aria-valuemax")).toBe("100");
  });

  it("shows custom status message when provided", () => {
    render(
      <IngestProgressBar stage="failed" progressPct={100} statusMessage="Parse error" />,
    );
    expect(screen.getByRole("progressbar", { name: "Parse error" })).toBeTruthy();
    expect(screen.getByText("Parse error")).toBeTruthy();
  });
});
