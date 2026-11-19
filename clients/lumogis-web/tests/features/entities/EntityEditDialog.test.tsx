// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-161 — Edit names dialog. The key property: the dialog sends alias
// *deltas* against the card it opened with, and never puts the old name in
// `aliases_add` (the server adds it).

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import type { EntityCard } from "../../../src/api/search";
import { EntityEditDialog } from "../../../src/features/entities/EntityEditDialog";
import { jsonResponse } from "../../helpers/jsonResponse";

const CARD: EntityCard = {
  entity_id: "e1",
  name: "Dr. Meier",
  type: "PERSON",
  aliases: ["Meier", "Hans"],
  summary: null,
  sources: [],
  scope: "personal",
  owner_user_id: null,
  share_status: "personal",
  is_owner: true,
  version: 3,
};

function setup(res: () => Response) {
  const fetchImpl = vi.fn(async (_url: string, _init?: RequestInit) => res());
  const client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const onSaved = vi.fn();
  const onClose = vi.fn();
  const onReload = vi.fn();
  render(
    <MemoryRouter>
      <div id="root" />
      <EntityEditDialog
        card={CARD}
        client={client}
        onClose={onClose}
        onSaved={onSaved}
        onReload={onReload}
      />
    </MemoryRouter>,
  );
  const sentBody = () => JSON.parse(String(fetchImpl.mock.calls[0]![1]!.body));
  return { fetchImpl, onSaved, onClose, onReload, sentBody };
}

const OK = () => jsonResponse(200, { ...CARD, name: "Hans Meier", version: 4 });

describe("EntityEditDialog", () => {
  it("sends only the delta: one name added, one removed; old name not in aliases_add", async () => {
    const { sentBody, onSaved } = setup(OK);
    await userEvent.click(screen.getByRole("button", { name: "Remove Hans" }));
    await userEvent.type(screen.getByLabelText("Add another name"), "Dr. M");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Hans Meier");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(sentBody()).toEqual({
      name: "Hans Meier",
      aliases_add: ["Dr. M"],
      aliases_remove: ["Hans"],
      version: 3,
    });
  });

  it("shows the old name as kept after a rename; removing it goes into aliases_remove with the warning", async () => {
    const { sentBody, onSaved } = setup(OK);
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Hans Meier");
    expect(screen.getByText("kept")).toBeInTheDocument();
    expect(screen.queryByText(/Lumogis may learn it again/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove Dr. Meier" }));
    expect(screen.getByText(/Lumogis may learn it again as a new entry/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(sentBody()).toEqual({
      name: "Hans Meier",
      aliases_add: [],
      aliases_remove: ["Dr. Meier"],
      version: 3,
    });
  });

  it("a plain rename sends no alias changes (the server keeps the old name)", async () => {
    const { sentBody, onSaved } = setup(OK);
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Hans Meier");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(sentBody()).toEqual({
      name: "Hans Meier",
      aliases_add: [],
      aliases_remove: [],
      version: 3,
    });
  });

  it("typing the old name into 'Add another name' after a rename does not put it in aliases_add", async () => {
    const { sentBody, onSaved } = setup(OK);
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Hans Meier");
    await userEvent.type(screen.getByLabelText("Add another name"), "dr. meier");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(sentBody().aliases_add).toEqual([]);
  });

  it("removing the old name and typing it back keeps it (not sent as removed)", async () => {
    const { sentBody, onSaved } = setup(OK);
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Hans Meier");
    await userEvent.click(screen.getByRole("button", { name: "Remove Dr. Meier" }));
    await userEvent.type(screen.getByLabelText("Add another name"), "Dr. Meier");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(sentBody().aliases_remove).toEqual([]);
  });

  it("an all-whitespace name shows the empty-name message and sends nothing", async () => {
    const { fetchImpl } = setup(OK);
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "   ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("The name can't be empty.")).toBeInTheDocument();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("Save is disabled until something changes", () => {
    setup(OK);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("409 version_conflict → reload message and a Reload button", async () => {
    const { onReload } = setup(() =>
      jsonResponse(409, {
        detail: { error: "version_conflict", code: "version_conflict", current_version: 5 },
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove Hans" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByText(/This entry changed since you opened it/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onReload).toHaveBeenCalled();
  });

  it("409 name_in_use through the real ApiClient links to the conflicting entry", async () => {
    setup(() =>
      jsonResponse(409, {
        detail: {
          error: "name_in_use",
          code: "name_in_use",
          conflicting_entity_id: "e9",
          conflicting_name: "Hans Meier",
          name: "Hans Meier",
          conflicting_is_staged: false,
        },
      }),
    );
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Hans Meier");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Another entry is already called “Hans Meier”/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View that entry" })).toHaveAttribute(
      "href",
      "/entities/e9",
    );
  });

  it("409 name_in_use for a staged entry uses the 'not sure about yet' wording", async () => {
    setup(() =>
      jsonResponse(409, {
        detail: {
          error: "name_in_use",
          code: "name_in_use",
          conflicting_entity_id: "e9",
          conflicting_name: "Hans Meier",
          name: "Hans Meier",
          conflicting_is_staged: true,
        },
      }),
    );
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Hans Meier");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/that it isn't sure about yet/)).toBeInTheDocument();
  });

  it("422 invalid_characters shows a field message; 503 shows the retry message", async () => {
    let status = 422;
    setup(() =>
      status === 422
        ? jsonResponse(422, {
            detail: { error: "invalid_characters", code: "invalid_characters" },
          })
        : jsonResponse(503, {
            detail: { error: "store_unavailable", code: "store_unavailable" },
          }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove Hans" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Names can't contain control characters.")).toBeInTheDocument();
    status = 503;
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Couldn't save. Try again.")).toBeInTheDocument();
  });

  it("Escape closes", async () => {
    const { onClose } = setup(OK);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
