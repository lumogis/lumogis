// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-966 — "Delete this entry" confirmation. Says what goes and what Lumogis
// will no longer learn, sends the card's version, and turns the server's
// refusals into plain words. Server-computed logic (cascades, block semantics)
// is proven by the live backend suites, not here.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import type { EntityCard } from "../../../src/api/search";
import { EntityDeleteDialog } from "../../../src/features/entities/EntityDeleteDialog";
import { jsonResponse } from "../../helpers/jsonResponse";

const CARD: EntityCard = {
  entity_id: "e1",
  name: "Meier Pharmacy",
  type: "PERSON",
  aliases: [],
  summary: null,
  sources: [],
  scope: "personal",
  owner_user_id: null,
  share_status: "personal",
  is_owner: true,
  version: 4,
};

const DELETED = {
  deleted: true,
  entity_id: "e1",
  blocked_names: ["Meier Pharmacy"],
  not_blocked: [],
  household_copy_removed: false,
  merges_removed: 0,
};

type Routes = {
  history?: () => Response;
  del?: () => Response;
};

function setup(routes: Routes = {}, card: EntityCard = CARD) {
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/history")) {
      return (routes.history ?? (() => jsonResponse(200, { items: [] })))();
    }
    if (init?.method === "DELETE") return (routes.del ?? (() => jsonResponse(200, DELETED)))();
    return jsonResponse(404, { detail: "not found" });
  });
  const client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const onClose = vi.fn();
  const onDeleted = vi.fn();
  render(
    <MemoryRouter>
      <div id="root" />
      <EntityDeleteDialog card={card} client={client} onClose={onClose} onDeleted={onDeleted} />
    </MemoryRouter>,
  );
  const deleteCalls = () =>
    fetchImpl.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "DELETE");
  return { fetchImpl, onClose, onDeleted, deleteCalls };
}

const MERGED_IN = {
  merge_id: "m1",
  direction: "merged_in",
  other_entity_id: "e9",
  other_name: "Meier Apotheke",
  origin: "member",
  merged_at: "2026-11-01T00:00:00Z",
  undone_at: null,
  can_undo: true,
  newer_merge_id: null,
  restores_sharing: false,
};

describe("EntityDeleteDialog (LUM-966)", () => {
  it("W1 says what goes and what Lumogis won't learn, in the ticket's words", () => {
    setup();
    expect(screen.getByRole("heading", { name: "Delete “Meier Pharmacy”?" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Removed from your list, search and chat. Lumogis won't learn “Meier Pharmacy” as a person again. Your documents are not changed.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Also removed for your household.")).not.toBeInTheDocument();
  });

  it("W1 uses the right article for the kind", () => {
    setup({}, { ...CARD, type: "ORG" });
    expect(screen.getByText(/as an organisation again/)).toBeInTheDocument();
  });

  it("W2 says the household copy goes too, for a shared entry", () => {
    setup({}, { ...CARD, share_status: "shared" });
    expect(screen.getByText("Also removed for your household.")).toBeInTheDocument();
  });

  it("W3 warns that an open merge can no longer be undone; a failed history read omits the line", async () => {
    setup({ history: () => jsonResponse(200, { items: [MERGED_IN] }) });
    expect(
      await screen.findByText("Merges into this entry can no longer be undone."),
    ).toBeInTheDocument();
  });

  it("W3 an undone merge or a merged_out item is not a warning", async () => {
    const { fetchImpl } = setup({
      history: () =>
        jsonResponse(200, {
          items: [
            { ...MERGED_IN, undone_at: "2026-11-02T00:00:00Z" },
            { ...MERGED_IN, merge_id: "m2", direction: "merged_out" },
          ],
        }),
    });
    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/can no longer be undone/)).not.toBeInTheDocument();
  });

  it("W3 a history failure leaves Delete enabled", async () => {
    const { fetchImpl } = setup({ history: () => jsonResponse(500, { detail: "boom" }) });
    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    expect(screen.queryByText(/can no longer be undone/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("W5 starts on Cancel, Escape closes, and Delete sends the card's version", async () => {
    const { onClose, onDeleted, deleteCalls } = setup();
    // Cancel is the first control in the dialog, which is where ModalFrame puts focus.
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveTextContent("Cancel");
    await waitFor(() => expect(buttons[0]).toHaveFocus());
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(DELETED));
    expect(deleteCalls()).toHaveLength(1);
    expect(String(deleteCalls()[0]![0])).toBe("/api/v1/kg/entities/e1?version=4");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("W5 Escape cancels without deleting", async () => {
    const { onClose, deleteCalls } = setup();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(deleteCalls()).toHaveLength(0);
  });

  it.each([
    [409, { detail: { error: "version_conflict", code: "version_conflict", current_version: 5 } }, /This entry changed since you opened it\. Close and try again\./],
    [503, { detail: { error: "store_unavailable", code: "store_unavailable" } }, /Couldn't delete right now — try again\./],
    [403, { detail: { error: "not_owner", code: "not_owner" } }, /Only the owner can delete this\. You can hide it from your list\./],
    [500, { detail: "boom" }, /Couldn't delete\. Try again\./],
  ])("W4 %s is said in plain words and keeps the dialog open", async (status, body, text) => {
    const { onDeleted } = setup({ del: () => jsonResponse(status, body) });
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(onDeleted).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled(); // can try again
  });
});
