// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-966 — what the entry card offers by who owns the entry: the owner gets
// "Delete…" in the "This is wrong…" menu; a member looking at another member's
// shared entry gets "Hide from my list" (no menu), and a hidden entry says so
// with "Show again". Only the full card is authoritative for `hidden`.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../../../src/api/client";
import type { EntityCard } from "../../../src/api/search";
import { EntityCardPanel } from "../../../src/features/memory/EntityCard";

const getEntityMock = vi.fn();
const getRelatedMock = vi.fn();
const hideMock = vi.fn();
const unhideMock = vi.fn();
const deleteMock = vi.fn();

vi.mock("../../../src/api/search", () => ({
  getEntity: (...a: unknown[]) => getEntityMock(...a),
  getRelatedEntities: (...a: unknown[]) => getRelatedMock(...a),
  getEntityHistory: () => Promise.resolve({ items: [] }),
  isEntityShared: (s: string | undefined) => s === "shared",
  publishEntity: vi.fn(),
  unpublishEntity: vi.fn(),
  editEntity: vi.fn(),
  hideEntity: (...a: unknown[]) => hideMock(...a),
  unhideEntity: (...a: unknown[]) => unhideMock(...a),
  deleteEntity: (...a: unknown[]) => deleteMock(...a),
  parseEntityEditError: () => null,
}));

const client = {} as ApiClient;

const OWN: EntityCard = {
  entity_id: "e1",
  name: "Total",
  type: "PERSON",
  scope: "personal",
  summary: null,
  aliases: [],
  sources: [],
  owner_user_id: null,
  share_status: "personal",
  is_owner: true,
  version: 3,
};

const THEIRS: EntityCard = {
  ...OWN,
  entity_id: "p1",
  name: "Houseplan",
  scope: "shared",
  owner_user_id: "alice",
  share_status: "shared",
  is_owner: false,
  version: 1,
};

function mount(props: Record<string, unknown> = {}, id = "e1") {
  return render(
    <MemoryRouter>
      <div id="root" />
      <EntityCardPanel entityId={id} client={client} {...props} />
    </MemoryRouter>,
  );
}

describe("EntityCardPanel — delete and hide (LUM-966)", () => {
  beforeEach(() => {
    for (const m of [getEntityMock, getRelatedMock, hideMock, unhideMock, deleteMock]) m.mockReset();
    getRelatedMock.mockResolvedValue({ related: [] });
  });

  it("W6 the owner finds Delete… in the 'This is wrong…' menu and no Hide button", async () => {
    getEntityMock.mockResolvedValue(OWN);
    mount({ allowEdit: true });
    await userEvent.click(await screen.findByRole("button", { name: /^This is wrong…/ }));
    expect(await screen.findByRole("menuitem", { name: "Delete…" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hide from my list" })).not.toBeInTheDocument();
  });

  it("W6 Delete… opens the confirmation, and a confirmed delete reports the server's answer", async () => {
    const answer = {
      deleted: true,
      entity_id: "e1",
      blocked_names: ["Total"],
      not_blocked: [],
      household_copy_removed: false,
      merges_removed: 0,
    };
    getEntityMock.mockResolvedValue(OWN);
    deleteMock.mockResolvedValue(answer);
    const onDeleted = vi.fn();
    mount({ allowEdit: true, onDeleted });
    await userEvent.click(await screen.findByRole("button", { name: /^This is wrong…/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    expect(await screen.findByRole("heading", { name: "Delete “Total”?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(answer));
    expect(deleteMock).toHaveBeenCalledWith(client, "e1", 3);
    expect(screen.queryByRole("heading", { name: "Delete “Total”?" })).not.toBeInTheDocument();
  });

  it("W6 a member viewing another member's shared entry sees Hide, not the menu", async () => {
    getEntityMock.mockResolvedValue(THEIRS);
    mount({ allowEdit: true }, "p1");
    expect(await screen.findByRole("button", { name: "Hide from my list" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^This is wrong…/ })).not.toBeInTheDocument();
  });

  it("W6 Hide calls the server, then the card and the list reload", async () => {
    getEntityMock.mockResolvedValue(THEIRS);
    hideMock.mockResolvedValue({ entity_id: "p1", hidden: true });
    const onEdited = vi.fn();
    mount({ allowEdit: true, onEdited }, "p1");
    await userEvent.click(await screen.findByRole("button", { name: "Hide from my list" }));
    await waitFor(() => expect(hideMock).toHaveBeenCalledWith(client, "p1"));
    await waitFor(() => expect(onEdited).toHaveBeenCalled());
    await waitFor(() => expect(getEntityMock.mock.calls.length).toBeGreaterThan(1));
  });

  it("W6 a hidden card shows the note and 'Show again', and no Hide button", async () => {
    getEntityMock.mockResolvedValue({ ...THEIRS, hidden: true });
    unhideMock.mockResolvedValue({ entity_id: "p1", hidden: false });
    mount({}, "p1");
    expect(await screen.findByText(/Hidden from your list\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hide from my list" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show again" }));
    await waitFor(() => expect(unhideMock).toHaveBeenCalledWith(client, "p1"));
  });

  it("W6 a search stub never claims an entry is hidden", async () => {
    getEntityMock.mockRejectedValue(new Error("boom"));
    mount({ initialCard: { ...THEIRS, hidden: true } }, "p1");
    // The stub says hidden, but only the full card is authoritative.
    expect(await screen.findByRole("button", { name: "Hide from my list" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show again" })).not.toBeInTheDocument();
  });

  it("a failed Hide says so and leaves the button", async () => {
    getEntityMock.mockResolvedValue(THEIRS);
    hideMock.mockRejectedValue(new Error("503"));
    mount({}, "p1");
    await userEvent.click(await screen.findByRole("button", { name: "Hide from my list" }));
    expect(await screen.findByText("Couldn't change that. Try again.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide from my list" })).toBeEnabled();
  });
});
