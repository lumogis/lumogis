// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-211 — app-level render error boundary.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { AppErrorBoundary } from "../../src/components/AppErrorBoundary";

function Boom(): JSX.Element {
  throw new Error("kaboom: raw internal detail that must not reach the user");
}

describe("AppErrorBoundary (LUM-211)", () => {
  beforeEach(() => {
    // React logs caught render errors; silence for a clean test run.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders children when there is no error", () => {
    render(
      <AppErrorBoundary>
        <p>healthy content</p>
      </AppErrorBoundary>,
    );
    expect(screen.getByText("healthy content")).toBeInTheDocument();
  });

  it("catches a render crash and shows a friendly, actionable fallback (no raw error)", () => {
    render(
      <AppErrorBoundary>
        <Boom />
      </AppErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reload/i })).toBeInTheDocument();
    // The raw thrown message is never surfaced to the user.
    expect(screen.queryByText(/kaboom/i)).toBeNull();
  });
});
