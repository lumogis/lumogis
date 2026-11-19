// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// useShellState (LUM-993 Pass 2a): reads GET /api/v1/me/shell, and
// refreshShellState() makes it read again at once (the Approvals page calls it
// after a decision, so the sidebar count does not lag).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const getJson = vi.fn();
vi.mock("../../src/auth/AuthProvider", () => ({ useAuth: () => ({ client: { getJson } }) }));

import { refreshShellState, useShellState } from "../../src/api/meShell";

function Probe(): JSX.Element {
  const s = useShellState();
  return <output>{s === undefined ? "none" : `pending ${s.pending_approvals}`}</output>;
}

const state = (pending: number) => ({
  email_connected: false,
  can_act: pending > 0,
  pending_approvals: pending,
  voice_available: false,
  online_ai_allowed: false,
  knowledge_graph_on: false,
});

describe("useShellState", () => {
  it("reads /api/v1/me/shell and re-reads on refreshShellState()", async () => {
    getJson.mockResolvedValueOnce(state(2)).mockResolvedValueOnce(state(0));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <Probe />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("none");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("pending 2"));
    expect(getJson).toHaveBeenCalledWith("/api/v1/me/shell");
    act(() => refreshShellState());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("pending 0"));
    expect(getJson).toHaveBeenCalledTimes(2);
  });
});
