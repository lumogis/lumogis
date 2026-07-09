// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-212 — shared skeleton / loading-placeholder primitives.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  LoadingPlaceholder,
  Skeleton,
  SkeletonText,
} from "../../../src/features/_shared/Skeleton";

describe("Skeleton primitives (LUM-212)", () => {
  it("LoadingPlaceholder exposes a single polite status with an sr-only label", () => {
    render(
      <LoadingPlaceholder label="Loading documents…">
        <Skeleton />
      </LoadingPlaceholder>,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status).toHaveAttribute("aria-live", "polite");
    // The label is announced to screen readers.
    expect(screen.getByText("Loading documents…")).toBeInTheDocument();
  });

  it("LoadingPlaceholder defaults the label to 'Loading…'", () => {
    render(
      <LoadingPlaceholder>
        <Skeleton />
      </LoadingPlaceholder>,
    );
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("Skeleton blocks are decorative (hidden from assistive tech)", () => {
    const { container } = render(<Skeleton width="50%" height="2rem" />);
    const span = container.querySelector("span");
    expect(span).not.toBeNull();
    expect(span).toHaveAttribute("aria-hidden", "true");
    expect(span).toHaveStyle({ width: "50%", height: "2rem" });
  });

  it("SkeletonText renders the requested number of lines, last one shortened", () => {
    const { container } = render(<SkeletonText lines={4} />);
    const lines = container.querySelectorAll("span[aria-hidden='true']");
    expect(lines).toHaveLength(4);
    // The final line is rendered narrower for realism.
    expect(lines[lines.length - 1]).toHaveStyle({ width: "60%" });
  });

  it("SkeletonText clamps to at least one line", () => {
    const { container } = render(<SkeletonText lines={0} />);
    expect(container.querySelectorAll("span[aria-hidden='true']")).toHaveLength(1);
  });
});
