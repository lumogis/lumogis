// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest — Phase 5F capture outbox (indexed storage adapter mocked).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import {
  __setCaptureOutboxBackendForTests,
  addAudioOutboxItem,
  addTextUrlOutboxItem,
  CaptureOutboxError,
  claimLegacyOutboxItem,
  discardOutboxItem,
  getOutboxStats,
  listLegacyOutboxItems,
  listOutboxItems,
  MAX_PENDING_VOICE_CLIPS,
  MAX_SINGLE_AUDIO_BYTES,
  syncOutbox,
} from "../../src/pwa/captureOutbox";

function installMemoryBackend(): Map<string, unknown> {
  const store = new Map<string, unknown>();
  __setCaptureOutboxBackendForTests({
    get: async (k) => store.get(k),
    set: async (k, v) => {
      store.set(k, v);
    },
    del: async (k) => {
      store.delete(k);
    },
  });
  return store;
}

function pathnameOf(input: RequestInfo): string {
  const s = String(input);
  try {
    return new URL(s).pathname;
  } catch {
    return s;
  }
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeClient(tokens: AccessTokenStore, fetchImpl: typeof fetch, sessionUser: string | null = "a"): ApiClient {
  const c = new ApiClient({ tokens, fetchImpl });
  c.setSessionUserId(sessionUser);
  return c;
}

/** Write a row straight into the backend, bypassing the owner requirement (legacy rows). */
function seedRow(store: Map<string, unknown>, id: string, extra: Record<string, unknown> = {}): void {
  const idx = (store.get("lumogis:capture-outbox:index") as string[] | undefined) ?? [];
  store.set("lumogis:capture-outbox:index", [...idx, id]);
  store.set(`lumogis:capture-outbox:meta:${id}`, {
    local_capture_id: id,
    kind: "text",
    text: `row ${id}`,
    created_at: "2026-11-08T00:00:00Z",
    status: "local_pending",
    last_error: null,
    ...extra,
  });
}

function capturesFetch(posts: Array<{ client_id: string; token: string | null }>, tokens: AccessTokenStore) {
  return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    if (pathnameOf(input) === "/api/v1/captures" && init?.method === "POST") {
      const body = JSON.parse(String(init?.body)) as { client_id: string };
      posts.push({ client_id: body.client_id, token: tokens.get() });
      return jsonResponse(201, { capture_id: `srv-${body.client_id}`, status: "pending" });
    }
    return jsonResponse(404, { detail: "unexpected" });
  });
}

beforeEach(() => {
  installMemoryBackend();
});

afterEach(() => {
  __setCaptureOutboxBackendForTests(null);
  vi.restoreAllMocks();
});

describe("captureOutbox", () => {
  it("saves text/url item to mocked store", async () => {
    const row = await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "hello" });
    expect(row.local_capture_id).toBeTruthy();
    expect(row.status).toBe("local_pending");

    const items = await listOutboxItems("a");
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "text", text: "hello" });

    const stats = await getOutboxStats("a");
    expect(stats.pendingCount).toBe(1);
  });

  it("saves audio item with Blob and stores blob key", async () => {
    const store = installMemoryBackend();
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" });
    const row = (await addAudioOutboxItem({ ownerUserId: "a", blob, mimeType: "audio/webm" })) as {
      local_attachment_id: string;
    };

    expect(row.local_attachment_id).toBeTruthy();
    const blobKey = `lumogis:capture-outbox:blob:${row.local_attachment_id}`;
    expect(store.get(blobKey)).toBeInstanceOf(Blob);
  });

  it("blocks when pending voice clip count exceeds cap", async () => {
    const small = new Blob([new Uint8Array([1])], { type: "audio/webm" });
    for (let i = 0; i < MAX_PENDING_VOICE_CLIPS; i += 1) {
      await addAudioOutboxItem({ ownerUserId: "a", blob: small, mimeType: "audio/webm" });
    }
    await expect(addAudioOutboxItem({ ownerUserId: "a", blob: small, mimeType: "audio/webm" })).rejects.toThrow(
      CaptureOutboxError,
    );
  });

  it("blocks when total voice bytes would exceed cap", async () => {
    const mib = 1024 * 1024;
    const chunk = 11 * mib;
    for (let i = 0; i < 9; i += 1) {
      const blob = new Blob([new Uint8Array(chunk)], { type: "audio/webm" });
      await addAudioOutboxItem({ ownerUserId: "a", blob, mimeType: "audio/webm" });
    }
    const overflow = new Blob([new Uint8Array(2 * mib)], { type: "audio/webm" });
    await expect(addAudioOutboxItem({ ownerUserId: "a", blob: overflow, mimeType: "audio/webm" })).rejects.toThrow(
      CaptureOutboxError,
    );
  });

  it("blocks single clip larger than server-aligned max", async () => {
    const huge = new Blob([new Uint8Array(MAX_SINGLE_AUDIO_BYTES + 1)], { type: "audio/webm" });
    await expect(addAudioOutboxItem({ ownerUserId: "a", blob: huge, mimeType: "audio/webm" })).rejects.toThrow(
      CaptureOutboxError,
    );
  });

  it("discard removes meta, index, and blob", async () => {
    const store = installMemoryBackend();
    const blob = new Blob([new Uint8Array([9])], { type: "audio/webm" });
    const row = (await addAudioOutboxItem({ ownerUserId: "a", blob, mimeType: "audio/webm" })) as {
      local_capture_id: string;
      local_attachment_id: string;
    };
    const blobKey = `lumogis:capture-outbox:blob:${row.local_attachment_id}`;
    expect(store.get(blobKey)).toBeInstanceOf(Blob);

    await discardOutboxItem(row.local_capture_id, "a");
    expect(store.get(`lumogis:capture-outbox:meta:${row.local_capture_id}`)).toBeUndefined();
    expect(store.get(blobKey)).toBeUndefined();
    expect(await listOutboxItems("a")).toHaveLength(0);
  });

  it("sync posts capture with client_id and removes row on success", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: unknown[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path === "/api/v1/captures" && init?.method === "POST") {
        posts.push(JSON.parse(String(init?.body)));
        return jsonResponse(201, { capture_id: "cap-srv", status: "pending" });
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = makeClient(tokens, fetchImpl as unknown as typeof fetch);

    await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "sync me" });
    const before = await listOutboxItems("a");
    const clientId = before[0]!.local_capture_id;

    const r = await syncOutbox(client, "a");
    expect(r.synced).toBe(1);
    expect(r.failed).toBe(0);

    expect(posts).toHaveLength(1);
    expect((posts[0] as { client_id: string }).client_id).toBe(clientId);
    expect((posts[0] as { text: string }).text).toBe("sync me");

    expect(await listOutboxItems("a")).toHaveLength(0);
  });

  it("sync posts audio with client_attachment_id in FormData", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let capturedAttachmentId: string | undefined;
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-srv", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-srv/attachments" && init?.method === "POST") {
        const fd = init!.body as FormData;
        capturedAttachmentId = fd.get("client_attachment_id") as string;
        return jsonResponse(201, {
          id: "att-1",
          attachment_type: "audio",
          mime_type: "audio/webm",
          size_bytes: 10,
          processing_status: "stored",
          created_at: "2026-04-30T12:00:00Z",
        });
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = makeClient(tokens, fetchImpl as unknown as typeof fetch);

    const blob = new Blob([new Uint8Array([1, 2])], { type: "audio/webm" });
    const added = (await addAudioOutboxItem({ ownerUserId: "a", blob, mimeType: "audio/webm" })) as {
      local_capture_id: string;
      local_attachment_id: string;
    };

    await syncOutbox(client, "a");

    expect(capturedAttachmentId).toBe(added.local_attachment_id);
    expect(await listOutboxItems("a")).toHaveLength(0);
  });

  it("sync failure marks failed and keeps item", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(401, { detail: "nope" });
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = makeClient(tokens, fetchImpl as unknown as typeof fetch);

    await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "x" });
    const r = await syncOutbox(client, "a");
    expect(r.failed).toBe(1);
    const items = await listOutboxItems("a");
    expect(items).toHaveLength(1);
    expect(items[0]!.status).toBe("failed");
    expect(items[0]!.last_error).toBeTruthy();
  });

  it("new text and audio rows carry owner_user_id", async () => {
    const t = await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "t" });
    const a = await addAudioOutboxItem({
      ownerUserId: "a",
      blob: new Blob([new Uint8Array([1])], { type: "audio/webm" }),
      mimeType: "audio/webm",
    });
    expect(t.owner_user_id).toBe("a");
    expect(a.owner_user_id).toBe("a");
  });

  it("add without ownerUserId throws no_owner", async () => {
    await expect(addTextUrlOutboxItem({ ownerUserId: "  ", kind: "text", text: "x" })).rejects.toMatchObject({
      code: "no_owner",
    });
    await expect(
      addAudioOutboxItem({
        ownerUserId: "",
        blob: new Blob([new Uint8Array([1])]),
        mimeType: "audio/webm",
      }),
    ).rejects.toMatchObject({ code: "no_owner" });
  });

  it("listOutboxItems and getOutboxStats return only the owner's rows", async () => {
    await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "a1" });
    await addTextUrlOutboxItem({ ownerUserId: "b", kind: "text", text: "b1" });
    await addTextUrlOutboxItem({ ownerUserId: "b", kind: "text", text: "b2" });
    expect(await listOutboxItems("a")).toHaveLength(1);
    expect(await listOutboxItems("b")).toHaveLength(2);
    expect((await getOutboxStats("a")).pendingCount).toBe(1);
    expect((await getOutboxStats("b")).pendingCount).toBe(2);
  });

  it("syncOutbox(client, 'b') does not upload or touch a's rows", async () => {
    const store = installMemoryBackend();
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const client = makeClient(tokens, capturesFetch(posts, tokens) as unknown as typeof fetch, "b");
    const row = await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "mine" });
    const r = await syncOutbox(client, "b");
    expect(posts).toHaveLength(0);
    expect(r).toMatchObject({ synced: 0, failed: 0, stopped: false });
    const meta = store.get(`lumogis:capture-outbox:meta:${row.local_capture_id}`) as {
      status: string;
      last_error: unknown;
    };
    expect(meta.status).toBe("local_pending");
    expect(meta.last_error).toBeNull();
  });

  it("legacy rows are excluded from list, stats and sync, and listed by listLegacyOutboxItems", async () => {
    const store = installMemoryBackend();
    seedRow(store, "legacy-1");
    await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "owned" });
    expect(await listOutboxItems("a")).toHaveLength(1);
    expect((await getOutboxStats("a")).pendingCount).toBe(1);
    expect((await listLegacyOutboxItems()).map((m) => m.local_capture_id)).toEqual(["legacy-1"]);

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const client = makeClient(tokens, capturesFetch(posts, tokens) as unknown as typeof fetch);
    await syncOutbox(client, "a");
    expect(posts.map((p) => p.client_id)).not.toContain("legacy-1");
    expect(await listLegacyOutboxItems()).toHaveLength(1);
  });

  it("claimLegacyOutboxItem sets the owner once; returns false for an owned row", async () => {
    const store = installMemoryBackend();
    seedRow(store, "legacy-1");
    expect(await claimLegacyOutboxItem("legacy-1", "a")).toBe(true);
    expect(await claimLegacyOutboxItem("legacy-1", "b")).toBe(false);
    expect(await listOutboxItems("a")).toHaveLength(1);
    expect(await listLegacyOutboxItems()).toHaveLength(0);
    expect(await claimLegacyOutboxItem("missing", "a")).toBe(false);
  });

  it("syncOutbox onlyIds syncs just the claimed row", async () => {
    const store = installMemoryBackend();
    seedRow(store, "legacy-1");
    await claimLegacyOutboxItem("legacy-1", "b");
    await addTextUrlOutboxItem({ ownerUserId: "b", kind: "text", text: "other pending" });
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const client = makeClient(tokens, capturesFetch(posts, tokens) as unknown as typeof fetch, "b");
    const r = await syncOutbox(client, "b", { onlyIds: ["legacy-1"] });
    expect(r.synced).toBe(1);
    expect(posts.map((p) => p.client_id)).toEqual(["legacy-1"]);
    expect(await listOutboxItems("b")).toHaveLength(1);
  });

  it("discardOutboxItem refuses another member's row with not_owner and the row survives", async () => {
    const row = await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "mine" });
    await expect(discardOutboxItem(row.local_capture_id, "b")).rejects.toMatchObject({ code: "not_owner" });
    expect(await listOutboxItems("a")).toHaveLength(1);
  });

  it("discardOutboxItem allows own and legacy rows, and a missing id is a no-op", async () => {
    const store = installMemoryBackend();
    seedRow(store, "legacy-1");
    const own = await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "mine" });
    await discardOutboxItem("legacy-1", "a");
    await discardOutboxItem(own.local_capture_id, "a");
    await expect(discardOutboxItem("nope", "a")).resolves.toBeUndefined();
    expect(await listLegacyOutboxItems()).toHaveLength(0);
    expect(await listOutboxItems("a")).toHaveLength(0);
    expect(store.get("lumogis:capture-outbox:index")).toEqual([]);
  });

  it("voice clip cap counts per owner", async () => {
    const small = new Blob([new Uint8Array([1])], { type: "audio/webm" });
    for (let i = 0; i < MAX_PENDING_VOICE_CLIPS; i += 1) {
      await addAudioOutboxItem({ ownerUserId: "a", blob: small, mimeType: "audio/webm" });
    }
    await expect(
      addAudioOutboxItem({ ownerUserId: "b", blob: small, mimeType: "audio/webm" }),
    ).resolves.toBeTruthy();
    await expect(
      addAudioOutboxItem({ ownerUserId: "a", blob: small, mimeType: "audio/webm" }),
    ).rejects.toMatchObject({ code: "too_many_voice_clips" });
  });

  it("voice byte cap is device-wide with neutral wording", async () => {
    const mib = 1024 * 1024;
    for (let i = 0; i < 9; i += 1) {
      await addAudioOutboxItem({
        ownerUserId: "a",
        title: "secret title of a",
        blob: new Blob([new Uint8Array(11 * mib)], { type: "audio/webm" }),
        mimeType: "audio/webm",
      });
    }
    const overflow = new Blob([new Uint8Array(2 * mib)], { type: "audio/webm" });
    const err = await addAudioOutboxItem({ ownerUserId: "b", blob: overflow, mimeType: "audio/webm" }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(CaptureOutboxError);
    const e = err as CaptureOutboxError;
    expect(e.code).toBe("voice_total_quota");
    expect(e.message).toContain("this device");
    expect(e.message).not.toContain("secret title");
    expect(e.message).not.toMatch(/\ba\b/);
  });

  it("sync skips a row whose owner changed after listing; not failed, not rewritten to syncing", async () => {
    const store = installMemoryBackend();
    const row = await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "x" });
    const metaKey = `lumogis:capture-outbox:meta:${row.local_capture_id}`;
    let reads = 0;
    __setCaptureOutboxBackendForTests({
      get: async (k) => {
        if (k === metaKey) {
          reads += 1;
          const v = store.get(k) as Record<string, unknown>;
          return reads >= 2 ? { ...v, owner_user_id: "b" } : v;
        }
        return store.get(k);
      },
      set: async (k, v) => {
        store.set(k, v);
      },
      del: async (k) => {
        store.delete(k);
      },
    });
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const client = makeClient(tokens, capturesFetch(posts, tokens) as unknown as typeof fetch);
    const r = await syncOutbox(client, "a");
    expect(posts).toHaveLength(0);
    expect(r).toMatchObject({ skipped: 1, failed: 0, synced: 0 });
    expect((store.get(metaKey) as { status: string }).status).toBe("local_pending");
  });

  it("a successful sync removes the row, blob and index entry", async () => {
    const store = installMemoryBackend();
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-srv", status: "pending" });
      }
      if (path.endsWith("/attachments")) {
        return jsonResponse(201, {
          id: "att",
          attachment_type: "audio",
          mime_type: "audio/webm",
          size_bytes: 1,
          processing_status: "stored",
          created_at: "2026-11-08T00:00:00Z",
        });
      }
      return jsonResponse(404, {});
    });
    const client = makeClient(tokens, fetchImpl as unknown as typeof fetch);
    const row = (await addAudioOutboxItem({
      ownerUserId: "a",
      blob: new Blob([new Uint8Array([1, 2])], { type: "audio/webm" }),
      mimeType: "audio/webm",
    })) as { local_capture_id: string; local_attachment_id: string };
    const r = await syncOutbox(client, "a");
    expect(r).toMatchObject({ synced: 1, failed: 0, stopped: false });
    expect(store.get(`lumogis:capture-outbox:meta:${row.local_capture_id}`)).toBeUndefined();
    expect(store.get(`lumogis:capture-outbox:blob:${row.local_attachment_id}`)).toBeUndefined();
    expect(store.get("lumogis:capture-outbox:index")).toEqual([]);
  });

  it("sync stops when the session member changes mid-loop (D14)", async () => {
    const store = installMemoryBackend();
    const ids: string[] = [];
    for (const t of ["one", "two", "three"]) {
      ids.push((await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: t })).local_capture_id);
    }
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const inner = capturesFetch(posts, tokens);
    // The session flips to "b" as soon as the first capture POST has resolved.
    const holder: { client?: ApiClient } = {};
    const flipping = (async (input: RequestInfo, init?: RequestInit) => {
      const res = await inner(input, init);
      holder.client?.setSessionUserId("b");
      return res;
    }) as unknown as typeof fetch;
    const client2 = makeClient(tokens, flipping);
    holder.client = client2;
    const r = await syncOutbox(client2, "a");
    expect(posts).toHaveLength(1);
    expect(r).toMatchObject({ synced: 1, stopped: true });
    for (const id of ids.slice(1)) {
      const m = store.get(`lumogis:capture-outbox:meta:${id}`) as { status: string; last_error: unknown };
      expect(m.status).toBe("local_pending");
      expect(m.last_error).toBeNull();
    }
  });

  it("sync with a session that is not the owner uploads nothing", async () => {
    await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "x" });
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const client = makeClient(tokens, capturesFetch(posts, tokens) as unknown as typeof fetch, null);
    const r = await syncOutbox(client, "a");
    expect(posts).toHaveLength(0);
    expect(r.stopped).toBe(true);
    const items = await listOutboxItems("a");
    expect(items[0]!.status).toBe("local_pending");
  });
});

describe("D14 pre-network checks restore the row (LUM-886 verify)", () => {
  /** A session that is "a" for the first `okCalls` reads, then "b". */
  function flipAfter(client: ApiClient, okCalls: number): void {
    let n = 0;
    vi.spyOn(client, "getSessionUserId").mockImplementation(() => {
      n += 1;
      return n <= okCalls ? "a" : "b";
    });
  }

  it("a session change just before createCapture puts the row back and posts nothing", async () => {
    const store = installMemoryBackend();
    const { local_capture_id: id } = await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "x" });
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const client = makeClient(tokens, capturesFetch(posts, tokens) as unknown as typeof fetch);
    // Reads: loop top (1), after the re-read (2) → "a"; just before createCapture (3) → "b".
    flipAfter(client, 2);
    const r = await syncOutbox(client, "a");
    expect(r).toMatchObject({ synced: 0, failed: 0, stopped: true });
    expect(posts).toHaveLength(0);
    const m = store.get(`lumogis:capture-outbox:meta:${id}`) as { status: string; last_error: unknown };
    expect(m.status).toBe("local_pending");
    expect(m.last_error).toBeNull();
  });

  it("a session change just before the audio upload keeps the row and uploads nothing", async () => {
    const store = installMemoryBackend();
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" });
    const { local_capture_id: id } = await addAudioOutboxItem({ ownerUserId: "a", blob, mimeType: "audio/webm" });
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const posts: Array<{ client_id: string; token: string | null }> = [];
    const fetchImpl = capturesFetch(posts, tokens);
    const client = makeClient(tokens, fetchImpl as unknown as typeof fetch);
    // Reads 1–3 → "a" (capture created); the read just before the upload (4) → "b".
    flipAfter(client, 3);
    const r = await syncOutbox(client, "a");
    expect(r).toMatchObject({ synced: 0, failed: 0, stopped: true });
    expect(posts).toHaveLength(1);
    expect(fetchImpl).toHaveBeenCalledTimes(1); // no attachment upload
    const m = store.get(`lumogis:capture-outbox:meta:${id}`) as { status: string; last_error: unknown };
    expect(m.status).toBe("local_pending");
    expect(m.last_error).toBeNull();
  });
});
