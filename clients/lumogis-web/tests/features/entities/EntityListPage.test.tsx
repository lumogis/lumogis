// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-161 — "What Lumogis knows" list page. Which rows a member may see is
// decided by the server (proven in pytest); this suite covers rendering,
// query params, paging and the zero / error states.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { EntityListPage } from "../../../src/features/entities/EntityListPage";
import { jsonResponse } from "../../helpers/jsonResponse";

let client: ApiClient;
vi.mock("../../../src/auth/AuthProvider", () => ({
  useAuth: () => ({ client }),
}));

function item(over: Record<string, unknown> = {}) {
  return {
    entity_id: "e1",
    name: "Dr. Meier",
    type: "PERSON",
    kind: "person",
    aliases: ["Meier", "Hans", "H. M.", "Doc"],
    alias_count: 6,
    scope: "personal",
    share_status: "personal",
    is_owner: true,
    mention_count: 3,
    last_mentioned_at: "2026-11-03T10:00:00Z",
    ...over,
  };
}

const COUNTS = { person: 2, org: 1, project: 0, concept: 0, other: 1 };

function mount(handler: (url: string) => Response) {
  const fetchImpl = vi.fn(async (url: string) => handler(String(url)));
  client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <EntityListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { fetchImpl };
}

describe("EntityListPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the heading, kind tabs with counts and rows", async () => {
    mount(() => jsonResponse(200, { entities: [item()], counts: COUNTS, total: 4 }));
    expect(await screen.findByRole("heading", { name: "What Lumogis knows" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "All (4)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "People (2)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Organisations (1)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Other (1)" })).toBeInTheDocument();
    const row = screen.getByTestId("entity-list-row");
    expect(within(row).getByRole("link", { name: "Dr. Meier" })).toHaveAttribute(
      "href",
      "/entities/e1",
    );
    // up to 3 other names, plus "+N more" from alias_count
    expect(within(row).getByText(/also: Meier, Hans, H\. M\. \+3 more/)).toBeInTheDocument();
    expect(within(row).getByText(/Mentioned in 3 items/)).toBeInTheDocument();
    expect(within(row).getByText(/last mentioned/)).toBeInTheDocument();
  });

  it("does not render counts or dates when the server sends null (another member's entry)", async () => {
    mount(() =>
      jsonResponse(200, {
        entities: [
          item({
            entity_id: "e2",
            name: "Shared Thing",
            is_owner: false,
            share_status: "shared",
            scope: "shared",
            mention_count: null,
            last_mentioned_at: null,
            aliases: [],
            alias_count: 0,
          }),
        ],
        counts: COUNTS,
        total: 1,
      }),
    );
    const row = await screen.findByTestId("entity-list-row");
    expect(within(row).queryByText(/Mentioned in/)).not.toBeInTheDocument();
    expect(within(row).queryByText(/last mentioned/)).not.toBeInTheDocument();
    expect(within(row).getByText("Shared by a household member")).toBeInTheDocument();
    expect(within(row).getByLabelText("Shared with household")).toBeInTheDocument();
  });

  it("kind tab, filter and sort go to the query string", async () => {
    const { fetchImpl } = mount(() =>
      jsonResponse(200, { entities: [item()], counts: COUNTS, total: 4 }),
    );
    await screen.findByRole("tab", { name: "People (2)" });
    await userEvent.click(screen.getByRole("tab", { name: "Organisations (1)" }));
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("kind=org"))).toBe(true),
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Filter" }));
    await userEvent.click(await screen.findByRole("option", { name: "Shared with household" }));
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("shared=only"))).toBe(true),
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Sort" }));
    await userEvent.click(await screen.findByRole("option", { name: "A–Z" }));
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("sort=name"))).toBe(true),
    );
  });

  it("Show more asks for the next page at the loaded offset", async () => {
    const first = Array.from({ length: 50 }, (_, i) => item({ entity_id: `a${i}`, name: `N${i}` }));
    const { fetchImpl } = mount((url) =>
      jsonResponse(200, {
        entities: url.includes("offset=50") ? [item({ entity_id: "z", name: "Last one" })] : first,
        counts: COUNTS,
        total: 51,
      }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Last one")).toBeInTheDocument();
    expect(String(fetchImpl.mock.calls[1]![0])).toContain("offset=50");
  });

  // VERIFY-PLAN: added — a failed "Show more" keeps the loaded rows (LUM-161 verify).
  it("a failed Show more keeps the rows already loaded", async () => {
    const first = Array.from({ length: 50 }, (_, i) => item({ entity_id: `a${i}`, name: `N${i}` }));
    mount((url) =>
      url.includes("offset=50")
        ? jsonResponse(503, { detail: { error: "store_unavailable", code: "store_unavailable" } })
        : jsonResponse(200, { entities: first, counts: COUNTS, total: 51 }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load more. Try again.");
    expect(screen.getByText("N0")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "All (4)" })).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load what Lumogis knows.")).not.toBeInTheDocument();
  });

  // VERIFY-PLAN: added — controls stay mounted while another kind loads (LUM-161 verify).
  it("keeps the tabs on screen while another kind loads", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((r) => (release = r));
    const fetchImpl = vi.fn(async (url: string) => {
      if (String(url).includes("kind=org")) await gate;
      return jsonResponse(200, { entities: [item()], counts: COUNTS, total: 4 });
    });
    client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <EntityListPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByRole("tab", { name: "Organisations (1)" }));
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("kind=org"))).toBe(true),
    );
    expect(screen.getByRole("tab", { name: "Organisations (1)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    release?.();
  });

  it("zero state when nothing has been learned", async () => {
    mount(() =>
      jsonResponse(200, {
        entities: [],
        counts: { person: 0, org: 0, project: 0, concept: 0, other: 0 },
        total: 0,
      }),
    );
    expect(await screen.findByText("Lumogis hasn't learned anything yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add documents" })).toHaveAttribute(
      "href",
      "/documents",
    );
  });

  it("a filter with no rows says so instead of showing the zero state", async () => {
    mount((url) =>
      url.includes("shared=only")
        ? jsonResponse(200, { entities: [], counts: COUNTS, total: 0 })
        : jsonResponse(200, { entities: [item()], counts: COUNTS, total: 4 }),
    );
    await screen.findByRole("tab", { name: "All (4)" });
    await userEvent.click(screen.getByRole("combobox", { name: "Filter" }));
    await userEvent.click(await screen.findByRole("option", { name: "Shared with household" }));
    expect(await screen.findByText("Nothing matches this filter.")).toBeInTheDocument();
    expect(screen.queryByText("Lumogis hasn't learned anything yet")).not.toBeInTheDocument();
  });

  it("503 shows an error with Retry, never the zero state", async () => {
    mount(() => jsonResponse(503, { detail: { error: "store_unavailable", code: "store_unavailable" } }));
    expect(await screen.findByText("Couldn't load what Lumogis knows.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.queryByText("Lumogis hasn't learned anything yet")).not.toBeInTheDocument();
  });
});

// ── LUM-966 — the notice after a delete ──────────────────────────────────────

import { DeletedNotice } from "../../../src/features/entities/EntityListPage";

describe("DeletedNotice (LUM-966)", () => {
  const base = {
    deleted: true,
    entity_id: "e1",
    blocked_names: ["Total"],
    not_blocked: [],
    household_copy_removed: false,
    merges_removed: 0,
  };

  it("W11 names the blocked names and links to the list", () => {
    render(
      <MemoryRouter>
        <DeletedNotice deleted={{ ...base, blocked_names: ["Total", "Total GmbH", "TOTAL AG"] }} />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(
        /Deleted\. “Total”, “Total GmbH” and “TOTAL AG” are on your list of names Lumogis won't learn\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See the list" })).toHaveAttribute("href", "/settings/wont-learn");
  });

  it("W11 says 'is' for one name and names what Lumogis keeps learning", () => {
    render(
      <MemoryRouter>
        <DeletedNotice
          deleted={{
            ...base,
            not_blocked: [
              { name: "Tot", reason: "used_by_other_entry" },
              { name: "Acme", reason: "already_corrected" },
            ],
          }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(/“Total” is on your list/)).toBeInTheDocument();
    expect(
      screen.getByText(
        "“Tot” is still used by another of your entries, so Lumogis keeps learning it for that entry.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/“Acme” already has a kind correction/)).toBeInTheDocument();
  });

  it("W11 the list page renders the notice from the router state and not otherwise", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { entities: [], counts: COUNTS, total: 0 }),
    );
    client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[{ pathname: "/entities", state: { deleted: base } }]}>
          <EntityListPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("entity-deleted-notice")).toBeInTheDocument();
  });
});
