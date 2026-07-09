// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-582 Rung 1 — ShareConversation: editable summary share, snapshot notice,
// non-owner read-only, can_share disable, edit-preservation prefill.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ShareConversation } from "../../../src/features/chat/ShareConversation";

const getConversation = vi.fn();
const publishConversation = vi.fn();
const unpublishConversation = vi.fn();

vi.mock("../../../src/api/conversations", async () => {
  const actual = await vi.importActual<typeof import("../../../src/api/conversations")>(
    "../../../src/api/conversations",
  );
  return {
    ...actual,
    getConversation: (...a: unknown[]) => getConversation(...a),
    publishConversation: (...a: unknown[]) => publishConversation(...a),
    unpublishConversation: (...a: unknown[]) => unpublishConversation(...a),
  };
});

const CLIENT = {} as never;

function detail(over: Record<string, unknown> = {}) {
  return {
    conversation_id: "c1",
    title: "Trip planning",
    summary: "AI summary of the trip",
    topics: [],
    entities: [],
    ended_at: "2026-06-01T00:00:00Z",
    scope: "personal",
    messages: [],
    share_status: "personal",
    is_owner: true,
    can_share: true,
    shared_summary: null,
    ...over,
  };
}

function renderShare() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ShareConversation client={CLIENT} conversationId="c1" />
    </QueryClientProvider>,
  );
}

describe("ShareConversation", () => {
  beforeEach(() => {
    getConversation.mockReset();
    publishConversation.mockReset().mockResolvedValue({});
    unpublishConversation.mockReset().mockResolvedValue(undefined);
  });
  afterEach(() => vi.restoreAllMocks());

  it("owner shares a personal conversation with an edited summary + snapshot notice", async () => {
    getConversation.mockResolvedValue(detail());
    const ev = userEvent.setup();
    renderShare();

    await ev.click(await screen.findByTestId("conversation-share-start"));
    const box = screen.getByTestId("conversation-share-summary");
    expect(box).toHaveValue("AI summary of the trip"); // prefilled from AI summary
    expect(screen.getByTestId("conversation-snapshot-notice")).toHaveTextContent(
      /new messages won.t be shared automatically/i,
    );

    await ev.clear(box);
    await ev.type(box, "Corrected household summary");
    await ev.click(screen.getByTestId("conversation-share-confirm"));

    await waitFor(() => expect(publishConversation).toHaveBeenCalledTimes(1));
    expect(publishConversation).toHaveBeenCalledWith(CLIENT, "c1", {
      shared_summary: "Corrected household summary",
    });
  });

  it("prefills the editor from the current shared_summary (preserves prior edit)", async () => {
    getConversation.mockResolvedValue(
      detail({ share_status: "shared", shared_summary: "Prior edit" }),
    );
    const ev = userEvent.setup();
    renderShare();

    expect(await screen.findByTestId("conversation-shared-badge")).toBeInTheDocument();
    await ev.click(screen.getByRole("button", { name: /edit summary/i }));
    expect(screen.getByTestId("conversation-share-summary")).toHaveValue("Prior edit");
  });

  it("owner can unshare a shared conversation", async () => {
    getConversation.mockResolvedValue(
      detail({ share_status: "shared", shared_summary: "x" }),
    );
    const ev = userEvent.setup();
    renderShare();
    await ev.click(await screen.findByTestId("conversation-unshare"));
    await waitFor(() => expect(unpublishConversation).toHaveBeenCalledWith(CLIENT, "c1"));
  });

  it("non-owner sees a read-only indicator, no share control", async () => {
    getConversation.mockResolvedValue(detail({ is_owner: false, share_status: "shared" }));
    renderShare();
    expect(await screen.findByTestId("conversation-share-indicator")).toHaveTextContent(
      "Shared with your household",
    );
    expect(screen.queryByTestId("conversation-share-start")).toBeNull();
    expect(screen.queryByTestId("conversation-unshare")).toBeNull();
  });

  it("a not-yet-summarized conversation cannot be shared", async () => {
    getConversation.mockResolvedValue(detail({ can_share: false }));
    renderShare();
    expect(await screen.findByTestId("conversation-share")).toHaveTextContent(
      /once it.s been summarized/i,
    );
    expect(screen.queryByTestId("conversation-share-start")).toBeNull();
  });
});
