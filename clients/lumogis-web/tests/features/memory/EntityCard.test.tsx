// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-211/512 code-review fix: a failed full-entity fetch must still surface an
// error + retry even when a partial `initialCard` (from search) is displayed —
// rather than silently leaving stale data.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import type { ApiClient } from "../../../src/api/client";
import type { EntityCard } from "../../../src/api/search";
import { EntityCardPanel } from "../../../src/features/memory/EntityCard";

const getEntityMock = vi.fn();
const getRelatedMock = vi.fn();
const editEntityMock = vi.fn();

vi.mock("../../../src/api/search", () => ({
  getEntity: (...a: unknown[]) => getEntityMock(...a),
  getRelatedEntities: (...a: unknown[]) => getRelatedMock(...a),
  // LUM-581 — EntityCard now renders <EntityShareToggle>, which imports these.
  isEntityShared: (s: string | undefined) => s === "shared",
  publishEntity: vi.fn(),
  unpublishEntity: vi.fn(),
  // LUM-161 — the card now renders <EntityEditDialog> (edit names).
  editEntity: (...a: unknown[]) => editEntityMock(...a),
  parseEntityEditError: () => null,
}));

const client = {} as ApiClient;

const INITIAL: EntityCard = {
  id: "e1",
  entity_id: "e1",
  name: "Ada Lovelace",
  type: "person",
  scope: "personal",
  summary: null,
  aliases: [],
  sources: [],
  owner_user_id: null,
} as unknown as EntityCard;

describe("EntityCardPanel — partial-card fetch failure (LUM-512 review fix)", () => {
  beforeEach(() => {
    getEntityMock.mockReset();
    getRelatedMock.mockReset();
  });

  it("shows an error + retry while still rendering the initialCard when the fetch fails", async () => {
    getEntityMock.mockRejectedValue(new Error("boom"));
    getRelatedMock.mockRejectedValue(new Error("boom"));

    render(
      <MemoryRouter>
        <EntityCardPanel entityId="e1" client={client} initialCard={INITIAL} />
      </MemoryRouter>,
    );

    // Partial data from search is still shown…
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    // …and the failure is surfaced with an actionable retry (not silently dropped).
    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert).toBeInTheDocument();
      expect(alert).toHaveTextContent(/unavailable|not found/i);
    });
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

// ── LUM-161 — "Edit names" button ───────────────────────────────────────────

const FULL: EntityCard = {
  ...INITIAL,
  aliases: ["Ada"],
  is_owner: true,
  share_status: "personal",
  version: 2,
} as unknown as EntityCard;

describe("EntityCardPanel — Edit names (LUM-161)", () => {
  beforeEach(() => {
    getEntityMock.mockReset();
    getRelatedMock.mockReset();
    editEntityMock.mockReset();
    getRelatedMock.mockResolvedValue({ related: [] });
  });

  function mount(props: Partial<React.ComponentProps<typeof EntityCardPanel>> = {}) {
    // Deliberately no QueryClientProvider: SearchPage renders the panel without one.
    return render(
      <MemoryRouter>
        <div id="root" />
        <EntityCardPanel entityId="e1" client={client} {...props} />
      </MemoryRouter>,
    );
  }

  it("offers Edit names only with allowEdit + owner + personal row + version", async () => {
    getEntityMock.mockResolvedValue(FULL);
    mount({ allowEdit: true });
    expect(await screen.findByRole("button", { name: "Edit names" })).toBeInTheDocument();
  });

  it("shows Personal once (the share control), with no separate scope badge", async () => {
    getEntityMock.mockResolvedValue(FULL);
    mount({ allowEdit: true });
    await screen.findByRole("button", { name: "Edit names" });
    expect(screen.getAllByText("Personal")).toHaveLength(1);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("has no Edit button without allowEdit (SearchPage usage)", async () => {
    getEntityMock.mockResolvedValue(FULL);
    mount();
    await screen.findByText("Ada Lovelace");
    await waitFor(() => expect(getEntityMock).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Edit names" })).not.toBeInTheDocument();
  });

  it.each([
    ["not the owner", { is_owner: false }],
    ["a shared row", { scope: "shared" }],
    ["no version", { version: null }],
  ])("has no Edit button when %s", async (_label, over) => {
    getEntityMock.mockResolvedValue({ ...FULL, ...over });
    mount({ allowEdit: true });
    await screen.findByText("Ada Lovelace");
    await waitFor(() => expect(getEntityMock).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Edit names" })).not.toBeInTheDocument();
  });

  it("after saving, re-fetches the card and calls onEdited", async () => {
    getEntityMock.mockResolvedValueOnce(FULL);
    getEntityMock.mockResolvedValue({ ...FULL, name: "Ada King", version: 3 });
    editEntityMock.mockResolvedValue({ ...FULL, name: "Ada King", version: 3 });
    const onEdited = vi.fn();
    mount({ allowEdit: true, onEdited });
    await userEvent.click(await screen.findByRole("button", { name: "Edit names" }));
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Ada King");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onEdited).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("heading", { name: "Ada King" })).toBeInTheDocument();
    expect(getEntityMock).toHaveBeenCalledTimes(2);
  });
});

// ── LUM-967 — "This is wrong…" menu (change what kind an entry is) ───────────

describe("EntityCardPanel — This is wrong… (LUM-967)", () => {
  beforeEach(() => {
    getEntityMock.mockReset();
    getRelatedMock.mockReset();
    editEntityMock.mockReset();
    getRelatedMock.mockResolvedValue({ related: [] });
  });

  function mount(props: Partial<React.ComponentProps<typeof EntityCardPanel>> = {}) {
    return render(
      <MemoryRouter>
        <div id="root" />
        <EntityCardPanel entityId="e1" client={client} {...props} />
      </MemoryRouter>,
    );
  }

  const wrongMenu = () => screen.queryByRole("button", { name: /^This is wrong…/ });

  it("is offered with allowEdit + owner + personal row + version, naming the current kind", async () => {
    getEntityMock.mockResolvedValue({ ...FULL, type: "PERSON" });
    mount({ allowEdit: true });
    await userEvent.click(await screen.findByRole("button", { name: /^This is wrong…/ }));
    expect(
      await screen.findByRole("menuitem", { name: "It's not a person, it's a…" }),
    ).toBeInTheDocument();
  });

  it("is absent without allowEdit (Search page), for a non-owner and for a shared row", async () => {
    getEntityMock.mockResolvedValue(FULL);
    const { unmount } = mount();
    await screen.findByText("Ada Lovelace");
    await waitFor(() => expect(getEntityMock).toHaveBeenCalled());
    expect(wrongMenu()).not.toBeInTheDocument();
    unmount();

    for (const over of [{ is_owner: false }, { scope: "shared" }]) {
      getEntityMock.mockResolvedValue({ ...FULL, ...over });
      const view = mount({ allowEdit: true });
      await screen.findByText("Ada Lovelace");
      await waitFor(() => expect(getEntityMock).toHaveBeenCalled());
      expect(wrongMenu()).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it("opens the kind dialog; after saving the card reloads and onEdited fires", async () => {
    getEntityMock.mockResolvedValueOnce({ ...FULL, type: "PERSON" });
    getEntityMock.mockResolvedValue({ ...FULL, type: "ORG", version: 3 });
    editEntityMock.mockResolvedValue({ ...FULL, type: "ORG", version: 3 });
    const onEdited = vi.fn();
    mount({ allowEdit: true, onEdited });
    await userEvent.click(await screen.findByRole("button", { name: /^This is wrong…/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /it's a…/ }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument(); // the menu closed behind the dialog
    await userEvent.click(await screen.findByRole("radio", { name: "Organisation" }));
    await userEvent.click(screen.getByRole("button", { name: "Change" }));
    await waitFor(() => expect(onEdited).toHaveBeenCalledTimes(1));
    expect(editEntityMock).toHaveBeenCalledWith(client, "e1", { entity_type: "ORG", version: 2 });
    expect(getEntityMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});

// ── LUM-965 — "It's the same as…" sits in the same menu with the same gating ──

describe("EntityCardPanel — It's the same as… (LUM-965)", () => {
  beforeEach(() => {
    getEntityMock.mockReset();
    getRelatedMock.mockReset();
    getRelatedMock.mockResolvedValue({ related: [] });
  });

  function mount(props: Partial<React.ComponentProps<typeof EntityCardPanel>> = {}) {
    return render(
      <MemoryRouter>
        <div id="root" />
        <EntityCardPanel entityId="e1" client={client} {...props} />
      </MemoryRouter>,
    );
  }

  it("W10 is offered next to the kind item, and opens the merge dialog", async () => {
    getEntityMock.mockResolvedValue({ ...FULL, type: "PERSON" });
    mount({ allowEdit: true });
    await userEvent.click(await screen.findByRole("button", { name: /^This is wrong…/ }));
    // Kind, merge and (LUM-966) Delete.
    expect(await screen.findAllByRole("menuitem")).toHaveLength(3);
    expect(await screen.findByRole("menuitem", { name: "Delete…" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("menuitem", { name: "It's the same as…" }));
    expect(await screen.findByTestId("entity-merge-dialog")).toBeInTheDocument();
  });

  it("W10 is absent whenever the kind item is: no allowEdit, non-owner, shared row, no version", async () => {
    for (const [props, over] of [
      [{}, {}],
      [{ allowEdit: true }, { is_owner: false }],
      [{ allowEdit: true }, { scope: "shared" }],
      [{ allowEdit: true }, { version: null }],
    ] as const) {
      getEntityMock.mockResolvedValue({ ...FULL, ...over });
      const view = mount(props);
      await screen.findByText("Ada Lovelace");
      await waitFor(() => expect(getEntityMock).toHaveBeenCalled());
      expect(screen.queryByRole("button", { name: /^This is wrong…/ })).not.toBeInTheDocument();
      view.unmount();
    }
  });
});
