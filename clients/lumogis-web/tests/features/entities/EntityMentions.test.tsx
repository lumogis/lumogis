// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-161 — "Mentioned in" section. Visibility (what the viewer may see) is
// computed by the server and proven in pytest, not here.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { EntityMentions, mentionCountsSentence } from "../../../src/features/entities/EntityMentions";
import { jsonResponse } from "../../helpers/jsonResponse";

function setup(handler: (url: string) => Response, isOwner = true) {
  const fetchImpl = vi.fn(async (url: string) => handler(String(url)));
  const client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <EntityMentions entityId="e1" client={client} isOwner={isOwner} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { fetchImpl };
}

const BODY = {
  mentions: [
    { kind: "document", ref_id: "doc-1", title: "Report.pdf", mentioned_at: "2026-11-03T10:00:00Z" },
    { kind: "chat", ref_id: "s-1", title: "Planning the trip", mentioned_at: "2026-11-02T10:00:00Z" },
    { kind: "ai_tool", ref_id: "mcp:add_entity:ab", title: "Added by an AI tool", mentioned_at: "2026-11-01T10:00:00Z" },
  ],
  counts: { document: 1, chat: 1, ai_tool: 1 },
  total: 3,
};

describe("EntityMentions", () => {
  it("renders the counts sentence, links only documents", async () => {
    setup(() => jsonResponse(200, BODY));
    expect(
      await screen.findByText("Mentioned in 1 document, 1 chat, 1 AI-tool addition"),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Report.pdf" });
    expect(link).toHaveAttribute("href", "/documents/doc-1");
    // chat + ai_tool are labels, not links
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText(/^Chat · /)).toBeInTheDocument();
    expect(screen.getByText(/^Added by an AI tool · /)).toBeInTheDocument();
  });

  it("pluralises and omits zero kinds", () => {
    expect(mentionCountsSentence({ document: 3, chat: 2, ai_tool: 0 })).toBe(
      "3 documents, 2 chats",
    );
  });

  it("shows the non-owner note only when the viewer is not the owner", async () => {
    setup(() => jsonResponse(200, BODY), false);
    expect(
      await screen.findByText("Only items shared with the household are listed."),
    ).toBeInTheDocument();
  });

  it("owner view has no non-owner note", async () => {
    setup(() => jsonResponse(200, BODY), true);
    await screen.findByText(/Mentioned in 1 document/);
    expect(screen.queryByText(/Only items shared/)).not.toBeInTheDocument();
  });

  it("says so when there is nothing the viewer can open", async () => {
    setup(() =>
      jsonResponse(200, { mentions: [], counts: { document: 0, chat: 0, ai_tool: 0 }, total: 0 }),
    );
    expect(await screen.findByText("No mentions you can open.")).toBeInTheDocument();
  });

  it("503 shows an error with Retry, not the empty message", async () => {
    let fail = true;
    const { fetchImpl } = setup(() =>
      fail
        ? jsonResponse(503, { detail: { error: "store_unavailable", code: "store_unavailable" } })
        : jsonResponse(200, BODY),
    );
    expect(await screen.findByText("Couldn't load mentions.")).toBeInTheDocument();
    expect(screen.queryByText("No mentions you can open.")).not.toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(screen.getByText(/Mentioned in 1 document/)).toBeInTheDocument());
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("Show more fetches the next page at the loaded offset", async () => {
    const page1 = {
      mentions: Array.from({ length: 50 }, (_, i) => ({
        kind: "document",
        ref_id: `d${i}`,
        title: `Doc ${i}`,
        mentioned_at: "2026-11-03T10:00:00Z",
      })),
      counts: { document: 51, chat: 0, ai_tool: 0 },
      total: 51,
    };
    const page2 = {
      mentions: [
        { kind: "document", ref_id: "d50", title: "Doc 50", mentioned_at: "2026-11-01T10:00:00Z" },
      ],
      counts: { document: 51, chat: 0, ai_tool: 0 },
      total: 51,
    };
    const { fetchImpl } = setup((url) =>
      jsonResponse(200, url.includes("offset=50") ? page2 : page1),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Doc 50")).toBeInTheDocument();
    expect(String(fetchImpl.mock.calls[1]![0])).toContain("offset=50");
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
  });
});
