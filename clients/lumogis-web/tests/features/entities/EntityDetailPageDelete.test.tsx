// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-966 — after the owner deletes an entry the detail page goes back to the
// list, which says what Lumogis won't learn (router state, not a query string).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { EntityDetailPage } from "../../../src/features/entities/EntityDetailPage";
import { DeletedNotice } from "../../../src/features/entities/EntityListPage";
import type { EntityDeleteResponse } from "../../../src/api/search";
import { jsonResponse } from "../../helpers/jsonResponse";

let client: ApiClient;
vi.mock("../../../src/auth/AuthProvider", () => ({
  useAuth: () => ({ client }),
}));

const CARD = {
  entity_id: "e1",
  name: "Total",
  type: "PERSON",
  aliases: [],
  summary: null,
  sources: [],
  scope: "personal",
  owner_user_id: null,
  share_status: "personal",
  is_owner: true,
  version: 3,
};

const ANSWER: EntityDeleteResponse = {
  deleted: true,
  entity_id: "e1",
  blocked_names: ["Total"],
  not_blocked: [{ name: "Tot", reason: "used_by_other_entry" }],
  household_copy_removed: false,
  merges_removed: 0,
};

function ListStub(): JSX.Element {
  const state = useLocation().state as { deleted?: EntityDeleteResponse } | null;
  return (
    <div data-testid="list-stub">
      {state?.deleted ? <DeletedNotice deleted={state.deleted} /> : "no notice"}
    </div>
  );
}

describe("EntityDetailPage — delete (LUM-966)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("W11 navigates to /entities with the notice after a confirmed delete", async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (init?.method === "DELETE") return jsonResponse(200, ANSWER);
      if (u.includes("/history")) return jsonResponse(200, { items: [] });
      if (u.includes("/related")) return jsonResponse(200, { related: [] });
      if (u.includes("/mentions")) {
        return jsonResponse(200, { mentions: [], counts: { document: 0, chat: 0, ai_tool: 0 }, total: 0 });
      }
      if (u.includes("/api/v1/kg/entities/e1")) return jsonResponse(200, CARD);
      return jsonResponse(404, { detail: "not found" });
    });
    client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/entities/e1"]}>
          <div id="root" />
          <Routes>
            <Route path="/entities/:entityId" element={<EntityDetailPage />} />
            <Route path="/entities" element={<ListStub />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /^This is wrong…/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getByTestId("list-stub")).toBeInTheDocument());
    expect(screen.getByText(/“Total” is on your list of names Lumogis won't learn/)).toBeInTheDocument();
    expect(screen.getByText(/“Tot” is still used by another of your entries/)).toBeInTheDocument();
  });
});
