// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// RouteFlash (LUM-993): a route-state toast shows once through sonner, is cleared
// from history state at once, and anything but a non-empty string is ignored.

import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const toast = vi.fn();
vi.mock("sonner", () => ({ toast: (msg: string) => toast(msg) }));

import { RouteFlash } from "../../src/components/RouteFlash";

function StateProbe(): JSX.Element {
  const loc = useLocation();
  return <output data-testid="state">{JSON.stringify(loc.state)}</output>;
}

function renderAt(state: unknown): void {
  render(
    <StrictMode>
      <MemoryRouter initialEntries={[{ pathname: "/me/profile", search: "?x=1", state }]}>
        <RouteFlash />
        <Routes>
          <Route path="*" element={<StateProbe />} />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  );
}

describe("RouteFlash", () => {
  beforeEach(() => toast.mockReset());

  it("shows the toast once under StrictMode and clears it from history state", async () => {
    renderAt({ toast: "Only household admins can open that page." });
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("null"));
    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenCalledWith("Only household admins can open that page.");
  });

  it("keeps the other route state", async () => {
    renderAt({ toast: "Hi.", fromOnboarding: true });
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent('{"fromOnboarding":true}'));
    expect(toast).toHaveBeenCalledTimes(1);
  });

  it.each([[{ toast: 42 }], [{ toast: "   " }], [{ toast: { html: "<b>x</b>" } }], [null]])(
    "ignores %j",
    async (state) => {
      renderAt(state);
      await new Promise((r) => setTimeout(r, 20));
      expect(toast).not.toHaveBeenCalled();
      expect(screen.getByTestId("state").textContent).toBe(JSON.stringify(state));
    },
  );
});
