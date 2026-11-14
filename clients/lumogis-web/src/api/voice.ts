// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-653 — standalone voice-transcribe web client. Posts a recorded audio blob
// as multipart/form-data to POST /api/v1/voice/transcribe (the whisper-sidecar
// STT route, ADR-031) and returns the transcript. The backend MIME allowlist
// already accepts audio/mp4, so the browser's Safari/iOS codec fallback works
// with no backend change.

import { ApiClient, ApiError } from "./client";
import type { components } from "./generated/openapi";

export type VoiceTranscribeResponse = components["schemas"]["VoiceTranscribeResponse"];

async function readErrorPayload(res: Response): Promise<string> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const m = (detail as { message?: unknown }).message;
      if (typeof m === "string") return m;
    }
    return JSON.stringify(detail ?? body);
  } catch {
    try {
      return await res.text();
    } catch {
      return res.statusText || "request failed";
    }
  }
}

/** Filename for the multipart part, keyed off the recorded codec. The backend
 * validates by content_type (not extension), but a consistent name keeps the
 * sidecar's mime→filename mapping tidy. */
export function defaultFilename(mime: string): string {
  if (mime.includes("mp4")) return "dictation.m4a";
  if (mime.includes("mpeg")) return "dictation.mp3";
  return "dictation.webm";
}

/**
 * Transcribe a recorded audio blob. Wraps the blob in a File so the multipart
 * part carries a sensible filename; never sets Content-Type manually (the
 * browser sets the multipart boundary). Throws ApiError on non-2xx.
 */
export async function transcribeVoice(
  client: ApiClient,
  blob: Blob,
  opts: { language?: string; filename?: string } = {},
): Promise<VoiceTranscribeResponse> {
  const filename = opts.filename ?? defaultFilename(blob.type);
  const file = new File([blob], filename, { type: blob.type });
  const fd = new FormData();
  fd.append("file", file);
  if (opts.language && opts.language.length > 0) {
    fd.append("language", opts.language);
  }
  const res = await client.fetch("/api/v1/voice/transcribe", {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorPayload(res));
  }
  return (await res.json()) as VoiceTranscribeResponse;
}
