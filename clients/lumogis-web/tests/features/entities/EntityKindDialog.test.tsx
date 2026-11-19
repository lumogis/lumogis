// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-967 — "It's not a person, it's a…" dialog. Sends exactly
// `{ entity_type, version }`, hides the entry's current kind, discloses the
// remembered correction, and turns the server's refusals into plain words.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import type { EntityCard } from "../../../src/api/search";
import {
  EntityKindDialog,
  kindNoun,
  withArticle,
} from "../../../src/features/entities/EntityKindDialog";
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

function setup(res: () => Response, card: EntityCard = CARD) {
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
      <EntityKindDialog
        card={card}
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

const OK = () => jsonResponse(200, { ...CARD, type: "ORG", version: 5 });

function conflict(extra: Record<string, unknown>) {
  return () =>
    jsonResponse(409, {
      detail: {
        error: "name_in_use",
        code: "name_in_use",
        conflicting_entity_id: "e2",
        conflicting_name: "Meier Pharmacy",
        name: "Meier Pharmacy",
        conflicting_is_staged: false,
        conflicting_entity_type: "ORG",
        ...extra,
      },
    });
}

async function pickOrgAndChange() {
  await userEvent.click(screen.getByRole("radio", { name: "Organisation" }));
  await userEvent.click(screen.getByRole("button", { name: "Change" }));
}

describe("kindNoun / withArticle", () => {
  it("names the kind in the singular, whatever the case", () => {
    expect(kindNoun("PERSON")).toBe("person");
    expect(kindNoun("org")).toBe("organisation");
    expect(kindNoun("CODING_DECISION")).toBe("coding decision");
    expect(kindNoun(null)).toBe("entry");
  });

  it("uses an before a vowel", () => {
    expect(withArticle("organisation")).toBe("an organisation");
    expect(withArticle("person")).toBe("a person");
    expect(withArticle("coding decision")).toBe("a coding decision");
    expect(withArticle(kindNoun(null))).toBe("an entry");
  });
});

describe("EntityKindDialog", () => {
  it("offers the three other kinds, not the current one", () => {
    setup(OK);
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    for (const name of ["Organisation", "Project", "Concept"]) {
      expect(screen.getByRole("radio", { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("radio", { name: "Person" })).not.toBeInTheDocument();
  });

  it("hides the current kind whatever its case", () => {
    setup(OK, { ...CARD, type: "org" });
    expect(screen.queryByRole("radio", { name: "Organisation" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(3);
  });

  it("keeps Change disabled until a kind is chosen, then sends exactly { entity_type, version }", async () => {
    const { sentBody, onSaved } = setup(OK);
    expect(screen.getByRole("button", { name: "Change" })).toBeDisabled();
    await pickOrgAndChange();
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(sentBody()).toEqual({ entity_type: "ORG", version: 4 });
    expect(onSaved.mock.calls[0]![0]).toMatchObject({ type: "ORG", version: 5 });
  });

  it("discloses the remembered correction, naming the entry and its current kind", () => {
    setup(OK);
    expect(
      screen.getByText(
        /Lumogis will remember this: when it reads “Meier Pharmacy” as a person again, it will store it as the kind you pick\. To undo, change it back\./,
      ),
    ).toBeInTheDocument();
  });

  it("shows the clash with an existing entry of the target kind, with a link to it", async () => {
    setup(conflict({}));
    await pickOrgAndChange();
    expect(
      await screen.findByText(
        /You already have an organisation called “Meier Pharmacy”\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Merge with “Meier Pharmacy”" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View that entry" })).toHaveAttribute(
      "href",
      "/entities/e2",
    );
  });

  it("says which other name clashes when it is not the main name", async () => {
    setup(conflict({ name: "Meier" }));
    await pickOrgAndChange();
    expect(
      await screen.findByText(
        /You already have an organisation called “Meier Pharmacy”, which also goes by “Meier”/,
      ),
    ).toBeInTheDocument();
  });

  it("explains a staged clash the way the rename dialog does", async () => {
    setup(conflict({ conflicting_is_staged: true }));
    await pickOrgAndChange();
    expect(
      await screen.findByText(
        "Lumogis has an organisation called “Meier Pharmacy” that it isn't sure about yet.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View that entry" })).toBeInTheDocument();
    // W9: a staged clash cannot be merged, so no merge button.
    expect(screen.queryByRole("button", { name: /^Merge with/ })).not.toBeInTheDocument();
  });

  it("offers Reload after a version conflict", async () => {
    const { onReload } = setup(() =>
      jsonResponse(409, {
        detail: { error: "version_conflict", code: "version_conflict", current_version: 6 },
      }),
    );
    await pickOrgAndChange();
    await userEvent.click(await screen.findByRole("button", { name: "Reload" }));
    expect(onReload).toHaveBeenCalled();
  });

  it.each([
    ["invalid_kind", 422],
    ["one_change_at_a_time", 422],
    ["store_unavailable", 503],
  ])("says it couldn't save on %s", async (code, status) => {
    setup(() => jsonResponse(status, { detail: { error: code, code } }));
    await pickOrgAndChange();
    expect(await screen.findByText("Couldn't save. Try again.")).toBeInTheDocument();
  });

  it("says it couldn't save on a network failure and does not call onSaved", async () => {
    const { onSaved } = setup(() => {
      throw new Error("net");
    });
    await pickOrgAndChange();
    expect(await screen.findByText("Couldn't save. Try again.")).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });
});


// ── LUM-965 — the "name in use" offer becomes "Merge with …" ─────────────────

describe("EntityKindDialog — merge offer (LUM-965)", () => {
  const SURVIVOR: EntityCard = {
    ...CARD,
    entity_id: "e2",
    name: "Meier Pharmacy",
    type: "ORG",
    version: 7,
  };

  function setupOffer(onMergedAway = vi.fn()) {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/kg/entities/e2") && (init?.method ?? "GET") === "GET") {
        return jsonResponse(200, SURVIVOR);
      }
      if (u.endsWith("/kg/entities/e2/merge")) {
        return jsonResponse(200, { entry: { ...SURVIVOR, version: 8 }, merge_id: "m1" });
      }
      return conflict({})();
    });
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    render(
      <MemoryRouter>
        <div id="root" />
        <EntityKindDialog
          card={CARD}
          client={client}
          onClose={vi.fn()}
          onSaved={vi.fn()}
          onMergedAway={onMergedAway}
        />
      </MemoryRouter>,
    );
    return { fetchImpl, onMergedAway };
  }

  it("W9 loads the survivor, merges this entry INTO it with both versions, then moves on", async () => {
    const { fetchImpl, onMergedAway } = setupOffer();
    await pickOrgAndChange();
    await userEvent.click(await screen.findByRole("button", { name: "Merge with “Meier Pharmacy”" }));
    // The merge dialog opens with the survivor = the clashing entry; the picker is skipped.
    expect(await screen.findByText(/Merge “Meier Pharmacy” into “Meier Pharmacy”\?/)).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(onMergedAway).toHaveBeenCalledWith("e2"));
    const merge = fetchImpl.mock.calls.find(([u]) => String(u).endsWith("/kg/entities/e2/merge"))!;
    expect(JSON.parse(String(merge[1]!.body))).toEqual({
      other_id: "e1",
      version: 7, // the survivor's
      other_version: 4, // this entry's
      keep_name: "this",
    });
  });

  it("W9 a failed load of the survivor shows the generic error and keeps the link", async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      if (String(url).endsWith("/kg/entities/e2")) return new Response("{}", { status: 500 });
      return conflict({})();
    });
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    render(
      <MemoryRouter>
        <div id="root" />
        <EntityKindDialog card={CARD} client={client} onClose={vi.fn()} onSaved={vi.fn()} />
      </MemoryRouter>,
    );
    await pickOrgAndChange();
    await userEvent.click(await screen.findByRole("button", { name: "Merge with “Meier Pharmacy”" }));
    expect(await screen.findByText("Couldn't open that entry to merge. Try again.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View that entry" })).toBeInTheDocument();
  });
});
