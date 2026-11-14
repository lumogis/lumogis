// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import { refineText } from "../../src/api/emailSend";

function makeClient(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ tokens: new AccessTokenStore(), fetchImpl });
}

function okJson(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("refineText", () => {
  it("POSTs {text,preset} to /api/v1/email/refine and returns {text,available}", async () => {
    const fetchImpl = vi.fn(async () => okJson({ text: "REFINED", available: true }));
    const client = makeClient(fetchImpl as unknown as typeof fetch);

    const res = await refineText(client, { text: "hi", preset: "formal" });

    expect(res).toEqual({ text: "REFINED", available: true });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/email/refine");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ text: "hi", preset: "formal" });
  });

  it("forwards a free-form instruction", async () => {
    const fetchImpl = vi.fn(async () => okJson({ text: "R", available: true }));
    const client = makeClient(fetchImpl as unknown as typeof fetch);
    await refineText(client, { text: "hi", instruction: "make it apologetic" });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string).instruction).toBe("make it apologetic");
  });

  it("surfaces available:false", async () => {
    const fetchImpl = vi.fn(async () => okJson({ text: "", available: false }));
    const client = makeClient(fetchImpl as unknown as typeof fetch);
    const res = await refineText(client, { text: "hi", preset: "shorter" });
    expect(res.available).toBe(false);
  });
});
