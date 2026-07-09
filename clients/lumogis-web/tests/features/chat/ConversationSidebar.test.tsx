// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../../../src/api/client";
import { ConversationSidebar } from "../../../src/features/chat/ConversationSidebar";
import { groupConversationByEndedAt } from "../../../src/features/chat/conversationGroups";

describe("groupConversationByEndedAt", () => {
  const now = new Date("2026-06-01T15:00:00Z").getTime();

  it("groups today", () => {
    const iso = new Date("2026-06-01T10:00:00Z").toISOString();
    expect(groupConversationByEndedAt(iso, now)).toBe("today");
  });

  it("groups yesterday", () => {
    const iso = new Date("2026-05-31T12:00:00Z").toISOString();
    expect(groupConversationByEndedAt(iso, now)).toBe("yesterday");
  });
});

describe("ConversationSidebar", () => {
  it("renders empty state when API returns no conversations", async () => {
    const client = {
      getJson: vi.fn().mockResolvedValue({ conversations: [] }),
    } as unknown as ApiClient;

    render(<ConversationSidebar client={client} onContinue={vi.fn()} />);
    expect(await screen.findByText(/Ended chats appear here/)).toBeTruthy();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the retry toast when DELETE returns partial:true (ADR-074 best-effort purge)", async () => {
    const conversation = {
      conversation_id: "11111111-1111-4111-8111-111111111111",
      title: "Roof repair quote",
      summary: "Second quote for the garage roof.",
      ended_at: new Date().toISOString(),
      scope: "personal",
      message_count: null,
    };
    const client = {
      getJson: vi.fn().mockResolvedValue({ conversations: [conversation] }),
      delete: vi.fn().mockResolvedValue({
        deleted: false,
        conversation_id: conversation.conversation_id,
        partial: true,
      }),
    } as unknown as ApiClient;

    // onDelete() guards on window.confirm(); accept it.
    vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    render(<ConversationSidebar client={client} onContinue={vi.fn()} />);

    const deleteButton = await screen.findByRole("button", {
      name: `Delete ${conversation.title}`,
    });
    await user.click(deleteButton);

    // Honest partial-failure UX: retry toast visible after the row is removed.
    expect(
      await screen.findByText(
        /Deletion incomplete — some copies may remain\. Tap to retry\./,
      ),
    ).toBeTruthy();
  });
});

describe("ConversationSidebar pending summaries (LUM-417)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  const PENDING_ID = "22222222-2222-4222-8222-222222222222";

  it("renders a Summarising… placeholder for a session awaiting its row", async () => {
    const client = {
      getJson: vi.fn().mockResolvedValue({ conversations: [] }),
    } as unknown as ApiClient;

    render(
      <ConversationSidebar
        client={client}
        onContinue={vi.fn()}
        pendingSummaries={[{ conversationId: PENDING_ID, title: "Roof repair" }]}
      />,
    );

    expect(await screen.findByTestId("pending-summary")).toBeTruthy();
    expect(screen.getByText("Roof repair")).toBeTruthy();
    expect(screen.getByText("Summarising…")).toBeTruthy();
    // The generic empty copy is suppressed while a pending row is showing.
    expect(screen.queryByText(/Ended chats appear here/)).toBeNull();
  });

  it("hides the placeholder and notifies the parent once the row arrives", async () => {
    const conversation = {
      conversation_id: PENDING_ID,
      title: "Roof repair",
      summary: "Quote for the garage roof.",
      ended_at: new Date().toISOString(),
      scope: "personal",
      message_count: null,
    };
    const client = {
      getJson: vi.fn().mockResolvedValue({ conversations: [conversation] }),
    } as unknown as ApiClient;
    const onPendingResolved = vi.fn();

    render(
      <ConversationSidebar
        client={client}
        onContinue={vi.fn()}
        pendingSummaries={[{ conversationId: PENDING_ID, title: "Roof repair" }]}
        onPendingResolved={onPendingResolved}
      />,
    );

    // Real summary row is present, so no placeholder is rendered…
    await screen.findByText("Quote for the garage roof.");
    expect(screen.queryByTestId("pending-summary")).toBeNull();
    // …and the parent is told the pending id resolved so it can prune state.
    await waitFor(() =>
      expect(onPendingResolved).toHaveBeenCalledWith([PENDING_ID]),
    );
  });

  it("polls the list while a summary is pending so it auto-resolves", async () => {
    vi.useFakeTimers();
    const conversation = {
      conversation_id: PENDING_ID,
      title: "Roof repair",
      summary: "Quote for the garage roof.",
      ended_at: new Date().toISOString(),
      scope: "personal",
      message_count: null,
    };
    // First fetch empty (row not written yet), later fetches return the row.
    const getJson = vi
      .fn()
      .mockResolvedValueOnce({ conversations: [] })
      .mockResolvedValue({ conversations: [conversation] });
    const client = { getJson } as unknown as ApiClient;

    render(
      <ConversationSidebar
        client={client}
        onContinue={vi.fn()}
        pendingSummaries={[{ conversationId: PENDING_ID, title: "Roof repair" }]}
        onPendingResolved={vi.fn()}
      />,
    );

    // Initial load resolves empty → placeholder visible.
    await vi.waitFor(() => expect(getJson).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("pending-summary")).toBeTruthy();

    // Advancing past one poll interval triggers a re-fetch that returns the row.
    await vi.advanceTimersByTimeAsync(3000);
    await vi.waitFor(() => expect(getJson).toHaveBeenCalledTimes(2));
    await vi.waitFor(() =>
      expect(screen.queryByTestId("pending-summary")).toBeNull(),
    );
  });
});
