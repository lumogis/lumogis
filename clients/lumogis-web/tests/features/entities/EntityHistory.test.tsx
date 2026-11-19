// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-965 — History section. Which merges can be undone, what undo restores and the
// graph are server-computed (pytest); here: the copy per origin, that Undo shows only
// where the server says `can_undo`, the confirm, and the move-mentions panel.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import type { EntityCard, EntityMergeHistoryItem } from "../../../src/api/search";
import { EntityHistory, historyLine } from "../../../src/features/entities/EntityHistory";
import { jsonResponse } from "../../helpers/jsonResponse";

const item = (over: Partial<EntityMergeHistoryItem>): EntityMergeHistoryItem => ({
  merge_id: "m1",
  direction: "merged_in",
  other_entity_id: "x1",
  other_name: "Dr. A. Meier",
  origin: "member",
  merged_at: "2026-11-10T10:00:00Z",
  undone_at: null,
  can_undo: true,
  newer_merge_id: null,
  restores_sharing: false,
  ...over,
});

const entry = (over: Partial<EntityCard>): EntityCard => ({
  entity_id: "e1",
  name: "Dr. Meier",
  type: "PERSON",
  aliases: [],
  summary: null,
  sources: [],
  scope: "personal",
  owner_user_id: null,
  share_status: "personal",
  is_owner: true,
  version: 5,
  ...over,
});

type Handler = (url: string, init?: RequestInit) => Response;

function setup(items: EntityMergeHistoryItem[], handler?: Handler, isOwner = true) {
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (handler) {
      const r = handler(u, init);
      if (r) return r;
    }
    return jsonResponse(200, { items });
  });
  const client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const onChanged = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <EntityHistory entityId="e1" client={client} isOwner={isOwner} onChanged={onChanged} />
    </QueryClientProvider>,
  );
  return { fetchImpl, onChanged, view, qc };
}

describe("historyLine", () => {
  it("W6 has its own copy per origin and for undone / merged-out rows", () => {
    expect(historyLine(item({}))).toMatch(/^Merged “Dr\. A\. Meier” into this entry on /);
    expect(historyLine(item({ origin: "auto_dedup" }))).toMatch(/^Merged automatically: /);
    expect(historyLine(item({ origin: "review_queue" }))).toMatch(/^Merged from the review queue: /);
    expect(historyLine(item({ origin: "admin" }))).toMatch(/^Merged by an admin: /);
    expect(historyLine(item({ undone_at: "2026-11-11T10:00:00Z" }))).toMatch(/— undone on /);
    expect(
      historyLine(
        item({ direction: "merged_out", other_name: "Dr. Meier", undone_at: "2026-11-11T10:00:00Z" }),
      ),
    ).toMatch(/^Was merged into “Dr\. Meier” on .*, undone on /);
  });
});

describe("EntityHistory", () => {
  it("W6 shows Undo only on the newest open merge; an older one names the newer to undo first", async () => {
    setup([
      item({ merge_id: "m2", other_name: "Newer One", can_undo: true }),
      item({ merge_id: "m1", other_name: "Older One", can_undo: false, newer_merge_id: "m2" }),
    ]);
    expect(await screen.findAllByRole("button", { name: "Undo merge" })).toHaveLength(1);
    expect(screen.getByText(/Undo the newer merge of “Newer One” first\./)).toBeInTheDocument();
  });

  it("W6 an undone row has no Undo and no hint", async () => {
    setup([item({ can_undo: false, undone_at: "2026-11-11T10:00:00Z" })]);
    await screen.findByText(/undone on/);
    expect(screen.queryByRole("button", { name: "Undo merge" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Undo the newer merge/)).not.toBeInTheDocument();
  });

  it("W6 a stale page: 409 undo_newer_first shows the same words and refetches", async () => {
    const { fetchImpl } = setup([item({})], (_url, init) =>
      init?.method === "POST"
        ? jsonResponse(409, {
            detail: {
              error: "undo_newer_first",
              code: "undo_newer_first",
              newer_merge_id: "m2",
              newer_other_name: "Newer One",
            },
          })
        : (undefined as unknown as Response),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Undo merge" }));
    const confirm = await screen.findByRole("alertdialog");
    await userEvent.click(within(confirm).getByRole("button", { name: "Undo merge" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Undo the newer merge of “Newer One” first.",
    );
    await waitFor(() =>
      expect(fetchImpl.mock.calls.filter(([, i]) => (i as RequestInit | undefined)?.method !== "POST")).toHaveLength(2),
    );
  });

  it("W7 the confirm names the consequences; the sharing sentence only when restores_sharing", async () => {
    setup([item({ restores_sharing: false })]);
    await userEvent.click(await screen.findByRole("button", { name: "Undo merge" }));
    expect(
      screen.getByText(/comes back as its own entry with its documents and links/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Lumogis won't merge the two again/)).toBeInTheDocument();
    expect(screen.queryByTestId("undo-sharing-line")).not.toBeInTheDocument();
  });

  it("W7 says the other entry will be shared again when undo restores sharing", async () => {
    setup([item({ restores_sharing: true })]);
    await userEvent.click(await screen.findByRole("button", { name: "Undo merge" }));
    expect(screen.getByTestId("undo-sharing-line")).toHaveTextContent(
      "will be shared with your household again, as it was before the merge",
    );
  });

  it("W7 undo → panel lists mentions since; moving sends the selected ids with both versions", async () => {
    const undone = {
      restored: entry({ entity_id: "x1", name: "Dr. A. Meier", version: 3 }),
      survivor: entry({ version: 5 }),
      mentions_since: [
        { kind: "document", ref_id: "d1", title: "Letter.pdf", mentioned_at: "2026-11-12T10:00:00Z", relation_id: 11 },
        { kind: "document", ref_id: "d2", title: "Invoice.pdf", mentioned_at: "2026-11-12T11:00:00Z", relation_id: 12 },
      ],
      skipped_rows: 1,
    };
    const { fetchImpl, onChanged } = setup([item({})], (url) => {
      if (url.endsWith("/undo")) return jsonResponse(200, undone);
      if (url.endsWith("/mentions/move")) {
        return jsonResponse(200, {
          moved: 1,
          source: entry({ version: 6 }),
          target: entry({ entity_id: "x1", version: 4 }),
        });
      }
      return undefined as unknown as Response;
    });
    await userEvent.click(await screen.findByRole("button", { name: "Undo merge" }));
    const confirm = await screen.findByRole("alertdialog");
    await userEvent.click(within(confirm).getByRole("button", { name: "Undo merge" }));
    expect(await screen.findByText("“Dr. A. Meier” is back as its own entry.")).toBeInTheDocument();
    expect(screen.getByTestId("undo-skipped-line")).toHaveTextContent(
      "Some items were deleted since the merge and could not be put back.",
    );
    expect(onChanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("checkbox", { name: /Invoice\.pdf/ }));
    await userEvent.click(screen.getByRole("button", { name: "Move to “Dr. A. Meier”" }));
    await screen.findByRole("status");
    const move = fetchImpl.mock.calls.find(([u]) => String(u).endsWith("/mentions/move"))!;
    expect(String(move[0])).toContain("/api/v1/kg/entities/e1/mentions/move");
    expect(JSON.parse(String(move[1]!.body))).toEqual({
      to_entity_id: "x1",
      relation_ids: [12],
      version: 5,
      to_version: 3,
    });
    expect(screen.getByRole("status")).toHaveTextContent("Moved 1 item to “Dr. A. Meier”.");
  });

  it("W8 renders nothing for a non-owner (and asks for nothing) and when there is no history", async () => {
    const { fetchImpl, view } = setup([item({})], undefined, false);
    expect(view.container).toBeEmptyDOMElement();
    expect(fetchImpl).not.toHaveBeenCalled();
    view.unmount();
    const empty = setup([]);
    // Wait for the query to settle: the component renders nothing while loading,
    // so checking right after the fetch would pass even if empty history rendered.
    await waitFor(() =>
      expect(empty.qc.getQueryState(["kg", "entities", "history", "e1"])?.status).toBe("success"),
    );
    expect(empty.view.container).toBeEmptyDOMElement();
  });
});
