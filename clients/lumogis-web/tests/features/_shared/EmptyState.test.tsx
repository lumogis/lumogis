// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { EmptyState } from "../../../src/features/_shared/EmptyState";

describe("EmptyState", () => {
  it("renders title, helperText, and fires primary onClick", () => {
    const onPrimary = vi.fn();
    render(
      <EmptyState
        title="Nothing here"
        helperText="Try adding items."
        actions={[{ label: "Add", onClick: onPrimary, primary: true }]}
      />,
    );
    expect(screen.getByRole("heading", { name: "Nothing here" })).toBeInTheDocument();
    expect(screen.getByText("Try adding items.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onPrimary).toHaveBeenCalledTimes(1);
  });

  it("renders internal Link for relative href", () => {
    render(
      <MemoryRouter>
        <EmptyState title="T" actions={[{ label: "Go", href: "/me/connectors" }]} />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Go" });
    expect(link).toHaveAttribute("href", "/me/connectors");
  });
});
