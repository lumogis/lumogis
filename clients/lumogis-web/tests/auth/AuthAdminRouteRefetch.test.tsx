// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — /admin/* refetches /auth/me when cache is older than 5s.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthAdminRouteRefetch } from "../../src/auth/AuthAdminRouteRefetch";

describe("AuthAdminRouteRefetch", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("calls invalidateQueries for ['auth','me'] when path is /admin and me data is stale (>5s)", async () => {
    vi.setSystemTime(0);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["auth", "me"], { id: "1", email: "a@a", role: "user" as const });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    vi.setSystemTime(10_000);

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/admin"]}>
          <AuthAdminRouteRefetch />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(spy).toHaveBeenCalled();
    });
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["auth", "me"], refetchType: "active" }),
    );
  });

  it("does not invalidate when navigating non-admin paths", async () => {
    vi.setSystemTime(0);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["auth", "me"], { id: "1", email: "a@a", role: "user" as const });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    vi.setSystemTime(10_000);

    function Shell(): JSX.Element {
      return <AuthAdminRouteRefetch />;
    }

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/me/profile"]}>
          <Routes>
            <Route path="/me/*" element={<Shell />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(spy).not.toHaveBeenCalled();
    });
  });
});
