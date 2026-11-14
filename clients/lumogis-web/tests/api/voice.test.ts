// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import { defaultFilename, transcribeVoice } from "../../src/api/voice";

function makeClient(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({
    tokens: new AccessTokenStore(),
    fetchImpl,
  });
}

function okJson(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("transcribeVoice", () => {
  it("posts multipart to /api/v1/voice/transcribe and returns the transcript", async () => {
    const fetchImpl = vi.fn(async () => okJson({ text: "hello", provider: "fake" }));
    const client = makeClient(fetchImpl as unknown as typeof fetch);
    const blob = new Blob(["a"], { type: "audio/webm" });

    const res = await transcribeVoice(client, blob);

    expect(res.text).toBe("hello");
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/voice/transcribe");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    // No manual Content-Type header (browser sets the multipart boundary).
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBeNull();
    const fd = init.body as FormData;
    expect(fd.get("file")).toBeInstanceOf(File);
    expect(fd.get("language")).toBeNull();
  });

  it("forwards language when set", async () => {
    const fetchImpl = vi.fn(async () => okJson({ text: "hola", provider: "fake" }));
    const client = makeClient(fetchImpl as unknown as typeof fetch);
    const blob = new Blob(["a"], { type: "audio/webm" });

    await transcribeVoice(client, blob, { language: "es" });

    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.body as FormData).get("language")).toBe("es");
  });

  it("throws ApiError with the mapped detail on non-2xx", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { code: "stt_disabled", message: "off" } }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const client = makeClient(fetchImpl as unknown as typeof fetch);
    const blob = new Blob(["a"], { type: "audio/webm" });

    await expect(transcribeVoice(client, blob)).rejects.toBeInstanceOf(ApiError);
  });

  it("defaultFilename maps codec to extension", () => {
    expect(defaultFilename("audio/mp4")).toBe("dictation.m4a");
    expect(defaultFilename("audio/webm;codecs=opus")).toBe("dictation.webm");
    expect(defaultFilename("audio/mpeg")).toBe("dictation.mp3");
  });
});
