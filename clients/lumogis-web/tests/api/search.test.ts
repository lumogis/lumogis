// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-161 — parseEntityEditError on the documented PATCH /api/v1/kg/entities/{id}
// error bodies, fed through the real ApiClient so `ApiError.code` / `.detail`
// have the shapes production produces.

import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import {
  editEntity,
  getEntityHistory,
  listEntities,
  mergeEntity,
  moveMentions,
  parseEntityEditError,
  undoMerge,
} from "../../src/api/search";
import { jsonResponse } from "../helpers/jsonResponse";

function clientReturning(res: Response): ApiClient {
  return new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl: vi.fn(async () => res) as unknown as typeof fetch,
  });
}

async function failWith(status: number, body: unknown): Promise<unknown> {
  try {
    await editEntity(clientReturning(jsonResponse(status, body)), "e1", { version: 1 });
  } catch (e) {
    return e;
  }
  throw new Error("expected editEntity to reject");
}

describe("parseEntityEditError", () => {
  it("decodes version_conflict with the current version", async () => {
    const e = await failWith(409, {
      detail: { error: "version_conflict", code: "version_conflict", current_version: 4 },
    });
    expect(parseEntityEditError(e)).toEqual({ code: "version_conflict", currentVersion: 4 });
  });

  it("decodes name_in_use with the conflicting entry", async () => {
    const e = await failWith(409, {
      detail: {
        error: "name_in_use",
        code: "name_in_use",
        conflicting_entity_id: "e2",
        conflicting_name: "Hans Meier",
        name: "Hans Meier",
        conflicting_is_staged: true,
      },
    });
    expect(parseEntityEditError(e)).toEqual({
      code: "name_in_use",
      conflictingEntityId: "e2",
      conflictingName: "Hans Meier",
      conflictingIsStaged: true,
      conflictingEntityType: null,
      name: "Hans Meier",
    });
  });

  it("decodes name_in_use from a kind change with the clashing entry's kind (LUM-967)", async () => {
    const e = await failWith(409, {
      detail: {
        error: "name_in_use",
        code: "name_in_use",
        conflicting_entity_id: "e2",
        conflicting_name: "Meier Pharmacy",
        name: "Meier",
        conflicting_is_staged: false,
        conflicting_entity_type: "ORG",
      },
    });
    expect(parseEntityEditError(e)).toEqual({
      code: "name_in_use",
      conflictingEntityId: "e2",
      conflictingName: "Meier Pharmacy",
      conflictingIsStaged: false,
      conflictingEntityType: "ORG",
      name: "Meier",
    });
  });

  it("decodes the kind-change 422s (LUM-967)", async () => {
    for (const code of ["invalid_kind", "one_change_at_a_time"]) {
      const e = await failWith(422, { detail: { error: code, code } });
      expect(parseEntityEditError(e)).toEqual({ code });
    }
  });

  it("decodes route-level 422 reasons and 503", async () => {
    for (const code of ["empty_name", "invalid_characters", "too_many_aliases", "nothing_to_change"]) {
      const e = await failWith(422, { detail: { error: code, code } });
      expect(parseEntityEditError(e)).toEqual({ code });
    }
    const e = await failWith(503, {
      detail: { error: "store_unavailable", code: "store_unavailable" },
    });
    expect(parseEntityEditError(e)).toEqual({ code: "store_unavailable" });
  });

  it("returns null for a Pydantic list body, an unknown code and non-ApiErrors", async () => {
    const pyd = await failWith(422, { detail: [{ type: "missing", loc: ["body"], msg: "x" }] });
    expect(parseEntityEditError(pyd)).toBeNull();
    const unknown = await failWith(500, { detail: { error: "boom", code: "boom" } });
    expect(parseEntityEditError(unknown)).toBeNull();
    expect(parseEntityEditError(new Error("net"))).toBeNull();
  });
});

describe("editEntity (kind change, LUM-967)", () => {
  it("PATCHes { entity_type, version } to the entry", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, { entity_id: "e/1", type: "ORG" }));
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    await editEntity(client, "e/1", { entity_type: "ORG", version: 4 });
    const calls = fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>;
    const [url, init] = calls[0]!;
    expect(url).toContain("/api/v1/kg/entities/e%2F1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ entity_type: "ORG", version: 4 });
  });
});

describe("listEntities", () => {
  it("sends only the params that are set", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { entities: [], counts: {}, total: 0 }),
    );
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    await listEntities(client, { kind: "org", sort: "name", limit: 50, offset: 50 });
    const url = String((fetchImpl.mock.calls[0] as unknown[])[0]);
    expect(url).toBe("/api/v1/kg/entities?kind=org&sort=name&limit=50&offset=50");
  });
});


// ── LUM-965 — merge, history, undo, move mentions ────────────────────────────

describe("merge errors (LUM-965)", () => {
  it("decodes the refusals with which entry they are about", async () => {
    for (const [status, code] of [
      [422, "same_entry"],
      [422, "staged_entry"],
      [409, "sharing_differs"],
      [409, "already_undone"],
      [404, "merge_not_found"],
      [409, "loser_id_taken"],
      [422, "relation_not_found"],
    ] as const) {
      const e = await failWith(status, { detail: { error: code, code, which: "other" } });
      expect(parseEntityEditError(e)).toEqual({ code, which: "other" });
    }
  });

  it("decodes a version conflict on the other entry and undo_newer_first", async () => {
    let e = await failWith(409, {
      detail: { error: "version_conflict", code: "version_conflict", current_version: 9, which: "other" },
    });
    expect(parseEntityEditError(e)).toEqual({
      code: "version_conflict",
      currentVersion: 9,
      which: "other",
    });
    e = await failWith(409, {
      detail: {
        error: "undo_newer_first",
        code: "undo_newer_first",
        newer_merge_id: "m2",
        newer_other_name: "Dr. A. Meier",
      },
    });
    expect(parseEntityEditError(e)).toEqual({
      code: "undo_newer_first",
      newerMergeId: "m2",
      newerOtherName: "Dr. A. Meier",
    });
  });
});

describe("merge requests (LUM-965)", () => {
  function recording(body: unknown = {}) {
    const fetchImpl = vi.fn(async () => jsonResponse(200, body));
    const client = new ApiClient({
      tokens: new AccessTokenStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const last = () => {
      const [url, init] = (fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>).at(-1)!;
      return { url, init };
    };
    return { client, last };
  }

  it("mergeEntity POSTs the survivor's merge path with exactly the four fields", async () => {
    const { client, last } = recording();
    await mergeEntity(client, "e/1", { other_id: "e2", version: 4, other_version: 2, keep_name: "this" });
    expect(last().url).toContain("/api/v1/kg/entities/e%2F1/merge");
    expect(last().init.method).toBe("POST");
    expect(JSON.parse(String(last().init.body))).toEqual({
      other_id: "e2",
      version: 4,
      other_version: 2,
      keep_name: "this",
    });
  });

  it("getEntityHistory GETs the entry's history", async () => {
    const { client, last } = recording({ items: [] });
    await getEntityHistory(client, "e1");
    expect(last().url).toContain("/api/v1/kg/entities/e1/history");
    expect(last().init.method).toBe("GET");
  });

  it("undoMerge POSTs to the merge's undo path", async () => {
    const { client, last } = recording();
    await undoMerge(client, "m/1");
    expect(last().url).toContain("/api/v1/kg/merges/m%2F1/undo");
    expect(last().init.method).toBe("POST");
  });

  it("moveMentions POSTs relation ids with both versions", async () => {
    const { client, last } = recording();
    await moveMentions(client, "e1", {
      to_entity_id: "e2",
      relation_ids: [3, 4],
      version: 5,
      to_version: 6,
    });
    expect(last().url).toContain("/api/v1/kg/entities/e1/mentions/move");
    expect(JSON.parse(String(last().init.body))).toEqual({
      to_entity_id: "e2",
      relation_ids: [3, 4],
      version: 5,
      to_version: 6,
    });
  });
});
