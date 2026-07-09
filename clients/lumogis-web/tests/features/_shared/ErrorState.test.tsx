// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-211 — shared error-state UI.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ErrorState } from "../../../src/features/_shared/ErrorState";

describe("ErrorState (LUM-211)", () => {
  it("announces an alert with a plain message and always offers an action", async () => {
    const onRetry = vi.fn();
    render(<ErrorState message="Lumogis couldn't load your documents." onRetry={onRetry} />);

    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(screen.getByText("Lumogis couldn't load your documents.")).toBeInTheDocument();
    // Generic, non-technical default title (no status code / stack).
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();

    const retry = screen.getByRole("button", { name: /try again/i });
    await userEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows the lumogis doctor hint by default and hides it when disabled", () => {
    const { rerender } = render(<ErrorState message="x" />);
    expect(screen.getByText(/lumogis doctor/i)).toBeInTheDocument();

    rerender(<ErrorState message="x" doctorHint={false} />);
    expect(screen.queryByText(/lumogis doctor/i)).toBeNull();
  });

  it("renders extra actions alongside retry", () => {
    render(
      <ErrorState
        message="Ollama is unreachable."
        onRetry={() => {}}
        actions={[{ label: "Use cloud LLM" }]}
      />,
    );
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /use cloud llm/i })).toBeInTheDocument();
  });
});
