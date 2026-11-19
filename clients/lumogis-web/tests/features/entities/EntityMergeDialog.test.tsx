// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-965 — "It's the same as…" dialog. Ownership, sharing rules and the undo record
// are decided and proven on the server (pytest, live Postgres); this file proves the
// request shape, the copy that tells the member what will happen first, and how the
// server's refusals are shown.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import type { EntityCard } from "../../../src/api/search";
import {
  EntityMergeDialog,
  mergeCandidates,
  sharingLine,
} from "../../../src/features/entities/EntityMergeDialog";
import { jsonResponse } from "../../helpers/jsonResponse";

const card = (over: Partial<EntityCard>): EntityCard => ({
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
  version: 4,
  ...over,
});

const THIS = card({});
const OTHER = card({ entity_id: "e2", name: "Dr. A. Meier", version: 2 });

type Handler = (url: string, init?: RequestInit) => Response;

function setup(handler: Handler, props: Partial<React.ComponentProps<typeof EntityMergeDialog>> = {}) {
  const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => handler(String(url), init));
  const client = new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const onMerged = vi.fn();
  const onClose = vi.fn();
  const onReload = vi.fn();
  render(
    <>
      <div id="root" />
      <EntityMergeDialog
        entry={THIS}
        client={client}
        onClose={onClose}
        onMerged={onMerged}
        onReload={onReload}
        {...props}
      />
    </>,
  );
  const mergeCall = () => fetchImpl.mock.calls.find(([u]) => String(u).endsWith("/merge"));
  return { fetchImpl, onMerged, onClose, onReload, mergeCall };
}

const OK = { entry: { ...THIS, version: 5 }, merge_id: "m1" };

describe("mergeCandidates", () => {
  it("W1 keeps only the member's own personal entries, never this one", () => {
    const hits = [
      card({ entity_id: "e1" }), // this entry
      card({ entity_id: "e2" }),
      card({ entity_id: "e3", is_owner: false, scope: "shared" }), // someone else's shared row
      card({ entity_id: "e4", scope: "shared" }), // a shared copy
      card({ entity_id: "e5", is_owner: false }),
    ];
    expect(mergeCandidates(hits, "e1").map((c) => c.entity_id)).toEqual(["e2"]);
  });
});

describe("EntityMergeDialog", () => {
  it("W1 picks from a search, loads the full card for its version, and lists only candidates", async () => {
    const { fetchImpl } = setup((url) => {
      if (url.includes("/kg/search")) {
        return jsonResponse(200, {
          entities: [
            { ...OTHER, version: null },
            card({ entity_id: "e1" }),
            card({ entity_id: "e9", name: "Theirs", is_owner: false, scope: "shared" }),
          ],
        });
      }
      return jsonResponse(200, OTHER); // GET /kg/entities/e2
    });
    await userEvent.type(screen.getByRole("searchbox"), "Meier");
    expect(await screen.findByRole("button", { name: /Dr\. A\. Meier/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Theirs/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Meier/ })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /Dr\. A\. Meier/ }));
    expect(await screen.findByText(/Merge “Dr\. A\. Meier” into “Dr\. Meier”\?/)).toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([u]) => String(u).endsWith("/kg/entities/e2"))).toBe(true);
  });

  it("W2 sends exactly {other_id, version, other_version, keep_name}; 'Keep other' sets keep_name", async () => {
    const { mergeCall, onMerged } = setup(() => jsonResponse(200, OK), { presetOther: OTHER });
    await userEvent.click(screen.getByRole("radio", { name: "Keep “Dr. A. Meier”" }));
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(onMerged).toHaveBeenCalledWith(OK));
    const [url, init] = mergeCall()!;
    expect(String(url)).toContain("/api/v1/kg/entities/e1/merge");
    expect(JSON.parse(String(init!.body))).toEqual({
      other_id: "e2",
      version: 4,
      other_version: 2,
      keep_name: "other",
    });
  });

  it("W2 defaults to keeping this entry's name", async () => {
    const { mergeCall } = setup(() => jsonResponse(200, OK), { presetOther: OTHER });
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(mergeCall()).toBeDefined());
    expect(JSON.parse(String(mergeCall()![1]!.body)).keep_name).toBe("this");
  });

  it("W3 shows the kind line only when the kinds differ, naming both", () => {
    setup(() => jsonResponse(200, OK), { presetOther: OTHER });
    expect(screen.queryByTestId("merge-kind-line")).not.toBeInTheDocument();
  });

  it("W3 names both kinds when they differ", () => {
    setup(() => jsonResponse(200, OK), { presetOther: card({ ...OTHER, type: "ORG" }) });
    const line = screen.getByTestId("merge-kind-line");
    expect(line).toHaveTextContent("“Dr. A. Meier” is an organisation");
    expect(line).toHaveTextContent("the entry stays a person");
    expect(line).toHaveTextContent("will remember that “Dr. A. Meier” is a person");
  });

  it("W4 says what becomes visible when exactly one side is shared (both directions)", () => {
    const shared = (c: EntityCard) => ({ ...c, share_status: "shared" as const });
    expect(sharingLine(shared(THIS), OTHER)).toBe(
      "“Dr. A. Meier” will be visible to your household as another name for “Dr. Meier”.",
    );
    expect(sharingLine(THIS, shared(OTHER))).toBe(
      "“Dr. Meier” will now be shared with your household.",
    );
    expect(sharingLine(THIS, OTHER)).toBeNull();
    expect(sharingLine(shared(THIS), shared(OTHER))).toBeNull();
  });

  it("W4 renders the sharing line in the dialog", () => {
    setup(() => jsonResponse(200, OK), {
      presetOther: { ...OTHER, share_status: "shared" },
    });
    expect(screen.getByTestId("merge-sharing-line")).toHaveTextContent(
      "“Dr. Meier” will now be shared with your household.",
    );
  });

  it("W5 a version conflict offers Reload", async () => {
    const { onReload } = setup(
      () =>
        jsonResponse(409, {
          detail: { error: "version_conflict", code: "version_conflict", current_version: 9, which: "other" },
        }),
      { presetOther: OTHER },
    );
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The other entry changed since you opened it");
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onReload).toHaveBeenCalled();
  });

  it("W5 a refusal for the other entry says it can't be merged", async () => {
    setup(
      () =>
        jsonResponse(403, {
          detail: { error: "not_owner", code: "not_owner", which: "other" },
        }),
      { presetOther: OTHER },
    );
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("'Dr. A. Meier' can't be merged");
    expect(alert).toHaveTextContent("not ones shared by someone else");
    expect(alert).toHaveAttribute("data-which", "other");
  });

  it("W5 the member's own household copy gets its own reason", async () => {
    setup(
      () =>
        jsonResponse(403, {
          detail: { error: "not_editable", code: "not_editable", which: "other", source_entity_id: "e9" },
        }),
      { presetOther: OTHER },
    );
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("'Dr. A. Meier' can't be merged");
    expect(alert).toHaveTextContent("household copy");
    expect(alert).not.toHaveTextContent("shared by someone else");
  });

  it("W5 a 404 for the other entry reloads the page (the merge may have landed already)", async () => {
    const { onReload } = setup(
      () =>
        jsonResponse(404, {
          detail: { error: "entity_not_found", code: "entity_not_found", which: "other" },
        }),
      { presetOther: OTHER },
    );
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(onReload).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert")).toHaveTextContent("may already have been merged");
  });

  it("W5 a staged entry is explained, not a generic error", async () => {
    setup(
      () =>
        jsonResponse(422, {
          detail: { error: "staged_entry", code: "staged_entry", which: "other" },
        }),
      { presetOther: OTHER },
    );
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That entry is still being checked and can't be merged yet.",
    );
  });

  it("with presetOther the picker is skipped and nothing is searched", () => {
    const { fetchImpl } = setup(() => jsonResponse(200, OK), { presetOther: OTHER });
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
