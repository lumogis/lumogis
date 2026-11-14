// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-648 (Fable R3 D6.1) — the notify_on_signals toggle must hit the REAL route
// `PATCH /api/v1/notifications/subscriptions/{id}` (there is NO `/preferences`
// suffix). A mocked wrong URL would pass while the real UI 404s, so assert the
// literal path the helper builds.

import { describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../../src/api/client";
import { deleteOwnWebPushRowForSubscription, patchWebPushSubscription } from "../../src/api/webPush";

describe("patchWebPushSubscription", () => {
  it("PATCHes /api/v1/notifications/subscriptions/{id} (no /preferences suffix)", async () => {
    const patchJson = vi.fn().mockResolvedValue({ id: 7, notify_on_signals: true });
    const client = { patchJson } as unknown as ApiClient;

    await patchWebPushSubscription(client, 7, { notify_on_signals: true });

    expect(patchJson).toHaveBeenCalledTimes(1);
    const [path, body] = patchJson.mock.calls[0]!;
    expect(path).toBe("/api/v1/notifications/subscriptions/7");
    expect(path).not.toContain("/preferences");
    expect(body).toEqual({ notify_on_signals: true });
  });
});

describe("deleteOwnWebPushRowForSubscription (LUM-886)", () => {
  function fakeSub(json: unknown): PushSubscription {
    return { toJSON: () => json } as unknown as PushSubscription;
  }

  it('returns "skipped" without a request when toJSON lacks keys', async () => {
    const fetchOnce = vi.fn();
    const client = { fetchOnce } as unknown as ApiClient;
    const out = await deleteOwnWebPushRowForSubscription(client, fakeSub({ endpoint: "https://push.example/a" }));
    expect(out).toBe("skipped");
    expect(fetchOnce).not.toHaveBeenCalled();
  });

  it("posts {endpoint, keys} only, then deletes the returned row", async () => {
    const fetchOnce = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 7, already_existed: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const client = { fetchOnce } as unknown as ApiClient;
    const out = await deleteOwnWebPushRowForSubscription(
      client,
      fakeSub({ endpoint: "https://push.example/a", keys: { p256dh: "p", auth: "a" } }),
    );
    expect(out).toBe("deleted");
    const [postPath, postInit] = fetchOnce.mock.calls[0]!;
    expect(postPath).toBe("/api/v1/notifications/subscribe");
    const body = JSON.parse(postInit.body as string) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["endpoint", "keys"]);
    const [delPath, delInit] = fetchOnce.mock.calls[1]!;
    expect(delPath).toBe("/api/v1/notifications/subscriptions/7");
    expect(delInit.method).toBe("DELETE");
  });

  it('returns "skipped" without a DELETE when /subscribe is not OK', async () => {
    const fetchOnce = vi.fn().mockResolvedValue(new Response("{}", { status: 503 }));
    const client = { fetchOnce } as unknown as ApiClient;
    const out = await deleteOwnWebPushRowForSubscription(
      client,
      fakeSub({ endpoint: "https://push.example/a", keys: { p256dh: "p", auth: "a" } }),
    );
    expect(out).toBe("skipped");
    expect(fetchOnce).toHaveBeenCalledTimes(1);
  });
});
