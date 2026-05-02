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
  discardOutboxItem,
  getOutboxStats,
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

beforeEach(() => {
  installMemoryBackend();
});

afterEach(() => {
  __setCaptureOutboxBackendForTests(null);
  vi.restoreAllMocks();
});

describe("captureOutbox", () => {
  it("saves text/url item to mocked store", async () => {
    const row = await addTextUrlOutboxItem({ kind: "text", text: "hello" });
    expect(row.local_capture_id).toBeTruthy();
    expect(row.status).toBe("local_pending");

    const items = await listOutboxItems();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "text", text: "hello" });

    const stats = await getOutboxStats();
    expect(stats.pendingCount).toBe(1);
  });

  it("saves audio item with Blob and stores blob key", async () => {
    const store = installMemoryBackend();
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" });
    const row = (await addAudioOutboxItem({ blob, mimeType: "audio/webm" })) as {
      local_attachment_id: string;
    };

    expect(row.local_attachment_id).toBeTruthy();
    const blobKey = `lumogis:capture-outbox:blob:${row.local_attachment_id}`;
    expect(store.get(blobKey)).toBeInstanceOf(Blob);
  });

  it("blocks when pending voice clip count exceeds cap", async () => {
    const small = new Blob([new Uint8Array([1])], { type: "audio/webm" });
    for (let i = 0; i < MAX_PENDING_VOICE_CLIPS; i += 1) {
      await addAudioOutboxItem({ blob: small, mimeType: "audio/webm" });
    }
    await expect(addAudioOutboxItem({ blob: small, mimeType: "audio/webm" })).rejects.toThrow(
      CaptureOutboxError,
    );
  });

  it("blocks when total voice bytes would exceed cap", async () => {
    const mib = 1024 * 1024;
    const chunk = 11 * mib;
    for (let i = 0; i < 9; i += 1) {
      const blob = new Blob([new Uint8Array(chunk)], { type: "audio/webm" });
      await addAudioOutboxItem({ blob, mimeType: "audio/webm" });
    }
    const overflow = new Blob([new Uint8Array(2 * mib)], { type: "audio/webm" });
    await expect(addAudioOutboxItem({ blob: overflow, mimeType: "audio/webm" })).rejects.toThrow(
      CaptureOutboxError,
    );
  });

  it("blocks single clip larger than server-aligned max", async () => {
    const huge = new Blob([new Uint8Array(MAX_SINGLE_AUDIO_BYTES + 1)], { type: "audio/webm" });
    await expect(addAudioOutboxItem({ blob: huge, mimeType: "audio/webm" })).rejects.toThrow(
      CaptureOutboxError,
    );
  });

  it("discard removes meta, index, and blob", async () => {
    const store = installMemoryBackend();
    const blob = new Blob([new Uint8Array([9])], { type: "audio/webm" });
    const row = (await addAudioOutboxItem({ blob, mimeType: "audio/webm" })) as {
      local_capture_id: string;
      local_attachment_id: string;
    };
    const blobKey = `lumogis:capture-outbox:blob:${row.local_attachment_id}`;
    expect(store.get(blobKey)).toBeInstanceOf(Blob);

    await discardOutboxItem(row.local_capture_id);
    expect(store.get(`lumogis:capture-outbox:meta:${row.local_capture_id}`)).toBeUndefined();
    expect(store.get(blobKey)).toBeUndefined();
    expect(await listOutboxItems()).toHaveLength(0);
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
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    await addTextUrlOutboxItem({ kind: "text", text: "sync me" });
    const before = await listOutboxItems();
    const clientId = before[0]!.local_capture_id;

    const r = await syncOutbox(client);
    expect(r.synced).toBe(1);
    expect(r.failed).toBe(0);

    expect(posts).toHaveLength(1);
    expect((posts[0] as { client_id: string }).client_id).toBe(clientId);
    expect((posts[0] as { text: string }).text).toBe("sync me");

    expect(await listOutboxItems()).toHaveLength(0);
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
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    const blob = new Blob([new Uint8Array([1, 2])], { type: "audio/webm" });
    const added = (await addAudioOutboxItem({ blob, mimeType: "audio/webm" })) as {
      local_capture_id: string;
      local_attachment_id: string;
    };

    await syncOutbox(client);

    expect(capturedAttachmentId).toBe(added.local_attachment_id);
    expect(await listOutboxItems()).toHaveLength(0);
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
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    await addTextUrlOutboxItem({ kind: "text", text: "x" });
    const r = await syncOutbox(client);
    expect(r.failed).toBe(1);
    const items = await listOutboxItems();
    expect(items).toHaveLength(1);
    expect(items[0]!.status).toBe("failed");
    expect(items[0]!.last_error).toBeTruthy();
  });
});
