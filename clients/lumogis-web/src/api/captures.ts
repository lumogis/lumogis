// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 5 capture API — QuickCapture UI (`POST /api/v1/captures`, attachments, transcribe).

import { ApiClient, ApiError } from "./client";
import type { components } from "./generated/openapi";

export type CaptureCreateRequest = components["schemas"]["CaptureCreateRequest"];
export type CaptureCreated = components["schemas"]["CaptureCreated"];
export type CaptureDetail = components["schemas"]["CaptureDetail"];
export type CaptureAttachmentSummary = components["schemas"]["CaptureAttachmentSummary"];
export type CaptureTranscriptSummary = components["schemas"]["CaptureTranscriptSummary"];
export type CaptureTranscribeRequest = components["schemas"]["CaptureTranscribeRequest"];

async function readErrorPayload(res: Response): Promise<string> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body?.detail ?? body);
  } catch {
    try {
      return await res.text();
    } catch {
      return res.statusText || "request failed";
    }
  }
}

/**
 * Turn API error payloads into user-facing copy. Supports FastAPI
 * `detail: { error: "…" }`, STT `detail: { code, message }`, and string detail.
 */
export function formatCaptureErrorMessage(status: number, detailPayload: string): string {
  if (status === 401) {
    return "You are signed out or your session expired. Sign in again.";
  }

  let raw: unknown;
  try {
    raw = JSON.parse(detailPayload);
  } catch {
    return detailPayload.length > 0 ? detailPayload : `Something went wrong (HTTP ${status}).`;
  }

  if (raw && typeof raw === "object" && "detail" in raw) {
    const inner = (raw as { detail: unknown }).detail;
    if (typeof inner === "string") {
      raw = inner;
    } else if (inner && typeof inner === "object") {
      raw = inner;
    }
  }

  if (typeof raw === "string") {
    return raw.length > 0 ? raw : `Something went wrong (HTTP ${status}).`;
  }

  if (raw && typeof raw === "object") {
    const o = raw as {
      error?: string;
      code?: string;
      message?: string;
    };
    if (typeof o.message === "string" && o.message.length > 0) {
      if (o.code === "stt_disabled" || o.code === "stt_processing_error") {
        return "Transcription is unavailable on the server. Your audio is still saved as an attachment if upload succeeded.";
      }
      return o.message;
    }
    if (typeof o.code === "string" && o.code.length > 0) {
      return `Request failed: ${o.code}`;
    }
    const err = o.error;
    if (typeof err === "string") {
      switch (err) {
        case "capture_not_found":
        case "attachment_not_found":
          return "That capture or attachment was not found.";
        case "attachment_blob_missing":
          return "The file is missing on the server.";
        case "capture_indexed":
          return "This capture is already in memory.";
        case "capture_transcript_required":
          return "Transcribe the audio first, then add to memory.";
        case "capture_no_indexable_content":
          return "Add a note, link, title, caption, or transcript — there is nothing to save to memory yet.";
        case "index_memory_unavailable":
          return "Memory search is temporarily unavailable. Try again later.";
        case "capture_invalid_state":
          return "This capture cannot be indexed in its current state.";
        case "file_too_large":
          return "File is too large for the server limit.";
        case "mime_type_not_allowed":
          return "This file type is not allowed.";
        case "capture_no_pending_audio":
          return "No audio is waiting for transcription, or all clips are already transcribed.";
        case "attachment_not_audio":
          return "That attachment is not audio; pick an audio file or recording.";
        case "capture_requires_text_or_url":
          return "Enter some text or a link to create the capture.";
        case "idempotency_key_conflict":
          return "This capture was already synced with different content. Discard the local copy or fix the conflict.";
        default:
          return err.replace(/_/g, " ");
      }
    }
  }

  return detailPayload.length > 0 ? detailPayload : `Something went wrong (HTTP ${status}).`;
}

export async function createCapture(
  client: ApiClient,
  body: CaptureCreateRequest,
): Promise<CaptureCreated> {
  return client.postJson<CaptureCreateRequest, CaptureCreated>("/api/v1/captures", body);
}

export async function getCapture(client: ApiClient, captureId: string): Promise<CaptureDetail> {
  return client.getJson<CaptureDetail>(`/api/v1/captures/${encodeURIComponent(captureId)}`);
}

export async function uploadCaptureAttachment(
  client: ApiClient,
  captureId: string,
  file: File,
  clientAttachmentId?: string,
): Promise<CaptureAttachmentSummary> {
  const fd = new FormData();
  fd.append("file", file);
  if (clientAttachmentId && clientAttachmentId.length > 0) {
    fd.append("client_attachment_id", clientAttachmentId);
  }
  const res = await client.fetch(`/api/v1/captures/${encodeURIComponent(captureId)}/attachments`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorPayload(res));
  }
  return (await res.json()) as CaptureAttachmentSummary;
}

export async function transcribeCapture(
  client: ApiClient,
  captureId: string,
  body: CaptureTranscribeRequest = {},
): Promise<CaptureTranscriptSummary> {
  return client.postJson<CaptureTranscribeRequest, CaptureTranscriptSummary>(
    `/api/v1/captures/${encodeURIComponent(captureId)}/transcribe`,
    body,
  );
}

export async function indexCapture(client: ApiClient, captureId: string): Promise<CaptureDetail> {
  return client.postJson<Record<string, never>, CaptureDetail>(
    `/api/v1/captures/${encodeURIComponent(captureId)}/index`,
    {},
  );
}
