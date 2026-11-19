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

async function readErrorPayload(res: Response): Promise<{ message: string; code?: string }> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    const detail = body?.detail;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object") {
      const code = (detail as { code?: unknown }).code;
      const m = (detail as { message?: unknown }).message;
      return {
        message: typeof m === "string" ? m : JSON.stringify(detail),
        code: typeof code === "string" ? code : undefined,
      };
    }
    return { message: JSON.stringify(detail ?? body) };
  } catch {
    try {
      return { message: await res.text() };
    } catch {
      return { message: res.statusText || "request failed" };
    }
  }
}

/**
 * The words a person sees when dictation fails, keyed on the route's `code`
 * (`{"detail": {"code": …}}`), never on the message text (LUM-995).
 */
export function dictationErrorMessage(err: unknown): string {
  const code = err instanceof ApiError ? err.code : undefined;
  switch (code) {
    case "stt_disabled":
      return "Speech-to-text is off on this Lumogis.";
    case "stt_model_missing":
      return "The speech model is still downloading. Try again in a minute.";
    case "stt_unreachable":
      return "The speech engine isn't answering yet. If this lasts more than a few minutes, ask whoever runs Lumogis to check it.";
    default:
      return "Transcription failed. Try again.";
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
    const { message, code } = await readErrorPayload(res);
    throw new ApiError(res.status, message, code);
  }
  return (await res.json()) as VoiceTranscribeResponse;
}
