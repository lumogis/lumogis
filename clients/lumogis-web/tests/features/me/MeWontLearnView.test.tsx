// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-966 — Settings → Names Lumogis won't learn. Three sections with empty
// states; removing a name is the undo of a delete and offers a re-read of the
// documents that mentioned it (force: true, one at a time, a failing one does
// not stop the rest). What the server blocks, restores and resolves is proven by
// the live backend suite.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { MeWontLearnView } from "../../../src/features/me/MeWontLearnView";
import { jsonResponse } from "../../helpers/jsonResponse";

let client: ApiClient;
vi.mock("../../../src/auth/AuthProvider", () => ({
  useAuth: () => ({ client }),
}));

const BLOCK = {
  name_norm: "total",
  display_name: "Total",
  entity_type: "PERSON",
  document_count: 3,
  created_at: "2026-11-17T00:00:00Z",
};
const CORRECTION = {
  name_norm: "acme",
  display_name: "Acme",
  from_type: "PERSON",
  to_type: "ORG",
};
const HIDDEN = {
  entity_id: "h1",
  name: "Bob's plan",
  type: "CONCEPT",
  hidden_at: "2026-11-17T00:00:00Z",
};

function rules(over: Record<string, unknown> = {}) {
  return {
    blocked: [BLOCK],
    corrections: [CORRECTION],
    hidden: [HIDDEN],
    blocked_total: 1,
    corrections_total: 1,
    hidden_total: 1,
    ...over,
  };
}

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function mount(handler: Handler, listBody: () => object = () => rules()) {
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const custom = handler(u, init);
    if (custom) return custom;
    if (u.includes("/api/v1/kg/name-rules") && !u.includes("/remove")) {
      return jsonResponse(200, listBody());
    }
    return jsonResponse(404, { detail: "not found" });
  });
  client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MeWontLearnView />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const calls = (needle: string) =>
    fetchImpl.mock.calls.filter(([u]) => String(u).includes(needle));
  return { fetchImpl, calls };
}

const REMOVED = {
  removed: true,
  restored_corrections: [],
  reread_documents: [
    { document_id: 7, title: "a.pdf" },
    { document_id: 8, title: "b.pdf" },
    { document_id: 9, title: "c.pdf" },
  ],
  reread_more: 0,
};

describe("MeWontLearnView (LUM-966)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("W7 renders the three sections", async () => {
    mount(() => undefined);
    expect(await screen.findByText("Total as a person")).toBeInTheDocument();
    expect(screen.getByText("Acme: person → organisation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Bob's plan" })).toHaveAttribute("href", "/entities/h1");
    expect(screen.getByRole("heading", { name: "Kind corrections" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Hidden from your list" })).toBeInTheDocument();
    expect(screen.getByText("Chats and notes are not re-read; only documents can be.")).toBeInTheDocument();
  });

  it("W7 each section has its own empty state", async () => {
    mount(
      () => undefined,
      () => rules({ blocked: [], corrections: [], hidden: [], blocked_total: 0, corrections_total: 0, hidden_total: 0 }),
    );
    expect(await screen.findByText(/Names you delete from What Lumogis knows will appear/)).toBeInTheDocument();
    expect(screen.getByText(/You haven't corrected what kind of thing anything is/)).toBeInTheDocument();
    expect(screen.getByText(/Use “Hide from my list” on a household entry/)).toBeInTheDocument();
  });

  it("W11 says when a list is cut", async () => {
    mount(
      () => undefined,
      () => rules({ blocked_total: 501, corrections_total: 600, hidden_total: 520 }),
    );
    expect(await screen.findByText("Showing 1 of 501")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 600")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 520")).toBeInTheDocument();
  });

  it("W8 removing a name offers a re-read; Re-read calls reingest with force once per document", async () => {
    const { calls } = mount((url, init) => {
      if (url.includes("/name-rules/remove")) return jsonResponse(200, REMOVED);
      if (url.includes("/reingest") && init?.method === "POST") {
        return jsonResponse(202, { status: "queued" });
      }
      return undefined;
    });
    await userEvent.click(await screen.findByRole("button", { name: "Remove Total" }));
    const offer = await screen.findByTestId("reread-offer");
    expect(within(offer).getByText("Re-read 3 documents that mentioned it?")).toBeInTheDocument();
    expect(JSON.parse(String(calls("/remove")[0]![1]!.body))).toEqual({
      kind: "block",
      name_norm: "total",
      entity_type: "PERSON",
    });
    await userEvent.click(within(offer).getByRole("button", { name: "Re-read" }));
    await waitFor(() => expect(calls("/reingest")).toHaveLength(3));
    expect(calls("/reingest").map(([u]) => String(u))).toEqual([
      "/api/v1/documents/7/reingest",
      "/api/v1/documents/8/reingest",
      "/api/v1/documents/9/reingest",
    ]);
    for (const [, init] of calls("/reingest")) {
      expect(JSON.parse(String((init as RequestInit).body))).toEqual({ force: true });
    }
    expect(await screen.findAllByText("Queued")).toHaveLength(3);
  });

  it("W8 a second removal starts a fresh offer, not the finished one", async () => {
    // VERIFY-PLAN: regression for the offer keeping the first removal's state.
    const { calls } = mount((url, init) => {
      if (url.includes("/name-rules/remove")) return jsonResponse(200, REMOVED);
      if (url.includes("/reingest") && init?.method === "POST") return jsonResponse(202, { status: "queued" });
      return undefined;
    });
    await userEvent.click(await screen.findByRole("button", { name: "Remove Total" }));
    await userEvent.click(within(await screen.findByTestId("reread-offer")).getByRole("button", { name: "Re-read" }));
    expect(await screen.findAllByText("Queued")).toHaveLength(3);
    await userEvent.click(await screen.findByRole("button", { name: "Remove Total" }));
    await waitFor(() => expect(calls("/remove")).toHaveLength(2));
    const offer = await screen.findByTestId("reread-offer");
    expect(await within(offer).findByRole("button", { name: "Re-read" })).toBeInTheDocument();
    expect(within(offer).queryByText("Queued")).not.toBeInTheDocument();
  });

  it("W8 a failing document shows its error and the rest continue", async () => {
    const { calls } = mount((url, init) => {
      if (url.includes("/name-rules/remove")) return jsonResponse(200, REMOVED);
      if (url.includes("/documents/8/reingest")) return jsonResponse(409, { detail: { code: "source_unavailable" } });
      if (url.includes("/reingest") && init?.method === "POST") return jsonResponse(202, { status: "queued" });
      return undefined;
    });
    await userEvent.click(await screen.findByRole("button", { name: "Remove Total" }));
    await userEvent.click(within(await screen.findByTestId("reread-offer")).getByRole("button", { name: "Re-read" }));
    await waitFor(() => expect(calls("/reingest")).toHaveLength(3));
    expect(await screen.findByText("Couldn't re-read this one")).toBeInTheDocument();
    expect(screen.getAllByText("Queued")).toHaveLength(2);
  });

  it("W8 'Not now' dismisses the offer without re-reading; a removal with no documents offers nothing", async () => {
    const { calls } = mount((url) =>
      url.includes("/name-rules/remove") ? jsonResponse(200, REMOVED) : undefined,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Remove Total" }));
    await userEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(screen.queryByTestId("reread-offer")).not.toBeInTheDocument();
    expect(calls("/reingest")).toHaveLength(0);
  });

  it("W8 says a restored kind correction applies again, and 'and N more' beyond the cap", async () => {
    mount((url) =>
      url.includes("/name-rules/remove")
        ? jsonResponse(200, {
            ...REMOVED,
            reread_more: 12,
            restored_corrections: [
              { name_norm: "acme", display_name: "Acme", from_type: "ORG", to_type: "PERSON" },
            ],
          })
        : undefined,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Remove Total" }));
    const offer = await screen.findByTestId("reread-offer");
    expect(within(offer).getByText("Your kind correction for “Acme” applies again.")).toBeInTheDocument();
    expect(within(offer).getByText(/And 12 more — re-read them from Documents\./)).toBeInTheDocument();
  });

  it("W9 removing a kind correction sends kind 'retype' with its from-kind and offers no re-read", async () => {
    const { calls } = mount((url) =>
      url.includes("/name-rules/remove")
        ? jsonResponse(200, { removed: true, restored_corrections: [], reread_documents: [], reread_more: 0 })
        : undefined,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Remove kind correction for Acme" }),
    );
    await waitFor(() => expect(calls("/remove")).toHaveLength(1));
    expect(JSON.parse(String(calls("/remove")[0]![1]!.body))).toEqual({
      kind: "retype",
      name_norm: "acme",
      entity_type: "PERSON",
    });
    expect(screen.queryByTestId("reread-offer")).not.toBeInTheDocument();
  });

  it("W9 'Show again' unhides the entry", async () => {
    const { calls } = mount((url, init) =>
      url.includes("/entities/h1/hide") && init?.method === "DELETE"
        ? jsonResponse(200, { entity_id: "h1", hidden: false })
        : undefined,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Show again" }));
    await waitFor(() => expect(calls("/entities/h1/hide")).toHaveLength(1));
  });

  it("an already-removed rule says so and reloads the list", async () => {
    const { calls } = mount((url) =>
      url.includes("/name-rules/remove")
        ? jsonResponse(404, { detail: { error: "rule_not_found", code: "rule_not_found" } })
        : undefined,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Remove Total" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Already removed.");
    await waitFor(() => expect(calls("/api/v1/kg/name-rules").length).toBeGreaterThan(2));
  });

  it("offers a retry when the list fails to load", async () => {
    mount((url) => (url.includes("/api/v1/kg/name-rules") ? jsonResponse(500, { detail: "boom" }) : undefined));
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
