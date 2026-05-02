// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 5F — local capture outbox (IndexedDB via idb-keyval) + manual sync only.
// No Cache Storage, no service worker, no Background Sync, no silent upload.

import { del, get, set } from "idb-keyval";

import type { ApiClient } from "../api/client";
import { ApiError } from "../api/client";
import { createCapture, formatCaptureErrorMessage, uploadCaptureAttachment } from "../api/captures";

/** Pending voice clips cap (plan §13). */
export const MAX_PENDING_VOICE_CLIPS = 10;

/** Total local voice payload cap (plan §13). */
export const MAX_LOCAL_VOICE_BYTES_TOTAL = 100 * 1024 * 1024;

/** Align with server `CAPTURE_MAX_AUDIO_BYTES` — single clip cannot upload if larger. */
export const MAX_SINGLE_AUDIO_BYTES = 26_214_400;

const INDEX_KEY = "lumogis:capture-outbox:index";

function metaKey(localCaptureId: string): string {
  return `lumogis:capture-outbox:meta:${localCaptureId}`;
}

function blobKey(localAttachmentId: string): string {
  return `lumogis:capture-outbox:blob:${localAttachmentId}`;
}

export type CaptureOutboxStatus = "local_pending" | "syncing" | "synced" | "failed";

/** Persisted metadata (blobs stored under {@link blobKey}). */
export type CaptureOutboxItemMeta =
  | CaptureOutboxTextUrlMeta
  | CaptureOutboxAudioMeta;

export interface CaptureOutboxTextUrlMeta {
  local_capture_id: string;
  kind: "text" | "url";
  title?: string;
  text?: string;
  url?: string;
  tags?: string[];
  created_at: string;
  status: CaptureOutboxStatus;
  last_error: string | null;
}

export interface CaptureOutboxAudioMeta {
  local_capture_id: string;
  local_attachment_id: string;
  kind: "audio";
  title?: string;
  text?: string;
  url?: string;
  tags?: string[];
  mime_type: string;
  size_bytes: number;
  duration_seconds?: number;
  created_at: string;
  status: CaptureOutboxStatus;
  last_error: null | string;
}

export type CaptureOutboxBackend = {
  get: (key: string) => Promise<unknown>;
  set: (key: string, value: unknown) => Promise<void>;
  del: (key: string) => Promise<void>;
};

let backendOverride: CaptureOutboxBackend | null = null;

function backend(): CaptureOutboxBackend {
  if (backendOverride) return backendOverride;
  return {
    get: (key: string) => get(key) as Promise<unknown>,
    set: (key: string, value: unknown) => set(key, value),
    del: (key: string) => del(key),
  };
}

/** Swap storage (Vitest). Pass `null` to restore default idb-keyval. */
export function __setCaptureOutboxBackendForTests(b: CaptureOutboxBackend | null): void {
  backendOverride = b;
}

export class CaptureOutboxError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "CaptureOutboxError";
  }
}

async function readIndex(): Promise<string[]> {
  const v = await backend().get(INDEX_KEY);
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

async function writeIndex(ids: string[]): Promise<void> {
  await backend().set(INDEX_KEY, ids);
}

async function appendToIndex(id: string): Promise<void> {
  const idx = await readIndex();
  if (!idx.includes(id)) idx.push(id);
  await writeIndex(idx);
}

export async function removeFromIndex(id: string): Promise<void> {
  const idx = await readIndex();
  await writeIndex(idx.filter((x) => x !== id));
}

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `loc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function countsTowardVoiceCap(m: CaptureOutboxItemMeta): boolean {
  return (
    m.kind === "audio" &&
    (m.status === "local_pending" || m.status === "failed" || m.status === "syncing")
  );
}

async function totalPendingVoiceBytes(): Promise<number> {
  const items = await listOutboxItems();
  return items.filter(countsTowardVoiceCap).reduce((s, m) => s + (m.kind === "audio" ? m.size_bytes : 0), 0);
}

async function pendingVoiceClipCount(): Promise<number> {
  const items = await listOutboxItems();
  return items.filter(countsTowardVoiceCap).length;
}

/** Public stats for UI (synced rows are removed from storage). */
export async function getOutboxStats(): Promise<{
  pendingCount: number;
  pendingAudioCount: number;
  audioBytes: number;
}> {
  const items = await listOutboxItems();
  const pending = items.filter(
    (m) => m.status === "local_pending" || m.status === "failed" || m.status === "syncing",
  );
  const audioPending = pending.filter((m): m is CaptureOutboxAudioMeta => m.kind === "audio");
  return {
    pendingCount: pending.length,
    pendingAudioCount: audioPending.length,
    audioBytes: audioPending.reduce((s, m) => s + m.size_bytes, 0),
  };
}

export async function listOutboxItems(): Promise<CaptureOutboxItemMeta[]> {
  const ids = await readIndex();
  const out: CaptureOutboxItemMeta[] = [];
  for (const id of ids) {
    const raw = await backend().get(metaKey(id));
    if (!raw || typeof raw !== "object") continue;
    out.push(raw as CaptureOutboxItemMeta);
  }
  return out;
}

async function saveMeta(m: CaptureOutboxItemMeta): Promise<void> {
  try {
    await backend().set(metaKey(m.local_capture_id), m);
  } catch (e) {
    throw new CaptureOutboxError(
      "idb_unavailable",
      e instanceof Error ? e.message : "Could not save to local storage.",
    );
  }
}

export async function discardOutboxItem(localCaptureId: string): Promise<void> {
  const raw = await backend().get(metaKey(localCaptureId));
  const meta = raw as CaptureOutboxItemMeta | undefined;
  if (meta?.kind === "audio") {
    try {
      await backend().del(blobKey(meta.local_attachment_id));
    } catch {
      /* soft */
    }
  }
  try {
    await backend().del(metaKey(localCaptureId));
  } catch {
    /* soft */
  }
  await removeFromIndex(localCaptureId);
}

/**
 * Text or URL capture for later sync. Requires non-empty text and/or URL after trim.
 */
export async function addTextUrlOutboxItem(input: {
  kind: "text" | "url";
  title?: string;
  text?: string;
  url?: string;
  tags?: string[] | null;
}): Promise<CaptureOutboxItemMeta> {
  const text = input.text?.trim() ?? "";
  const url = input.url?.trim() ?? "";
  if (!text && !url) {
    throw new CaptureOutboxError("validation", "Enter a note or a URL to save locally.");
  }
  const local_capture_id = newId();
  const meta: CaptureOutboxTextUrlMeta = {
    local_capture_id,
    kind: input.kind,
    title: input.title?.trim() || undefined,
    text: text || undefined,
    url: url || undefined,
    tags: input.tags?.length ? input.tags : undefined,
    created_at: new Date().toISOString(),
    status: "local_pending",
    last_error: null,
  };
  await appendToIndex(local_capture_id);
  await saveMeta(meta);
  return meta;
}

/**
 * Voice row: stores Blob under `blob:${local_attachment_id}`.
 */
export async function addAudioOutboxItem(input: {
  blob: Blob;
  mimeType: string;
  title?: string;
  text?: string;
  url?: string;
  tags?: string[] | null;
  durationSeconds?: number;
}): Promise<CaptureOutboxItemMeta> {
  const size = input.blob.size;
  if (size <= 0) {
    throw new CaptureOutboxError("validation", "Audio recording is empty.");
  }
  if (size > MAX_SINGLE_AUDIO_BYTES) {
    throw new CaptureOutboxError(
      "voice_payload_too_large",
      `This clip is too large (${(size / 1024 / 1024).toFixed(1)} MiB). The server limit is ${(MAX_SINGLE_AUDIO_BYTES / 1024 / 1024).toFixed(0)} MiB per file.`,
    );
  }

  const n = await pendingVoiceClipCount();
  if (n >= MAX_PENDING_VOICE_CLIPS) {
    throw new CaptureOutboxError(
      "too_many_voice_clips",
      `You already have ${MAX_PENDING_VOICE_CLIPS} pending voice clips. Sync or discard some before adding more.`,
    );
  }

  const total = await totalPendingVoiceBytes();
  if (total + size > MAX_LOCAL_VOICE_BYTES_TOTAL) {
    throw new CaptureOutboxError(
      "voice_total_quota",
      "Local voice storage would exceed the limit (100 MiB). Sync or discard clips first.",
    );
  }

  const local_capture_id = newId();
  const local_attachment_id = newId();

  try {
    await backend().set(blobKey(local_attachment_id), input.blob);
  } catch (e) {
    throw new CaptureOutboxError(
      "idb_unavailable",
      e instanceof Error ? e.message : "Could not save audio on this device.",
    );
  }

  const meta: CaptureOutboxAudioMeta = {
    local_capture_id,
    local_attachment_id,
    kind: "audio",
    title: input.title?.trim() || undefined,
    text: input.text?.trim() || undefined,
    url: input.url?.trim() || undefined,
    tags: input.tags?.length ? input.tags : undefined,
    mime_type: input.mimeType,
    size_bytes: size,
    duration_seconds: input.durationSeconds,
    created_at: new Date().toISOString(),
    status: "local_pending",
    last_error: null,
  };

  try {
    await appendToIndex(local_capture_id);
    await saveMeta(meta);
  } catch (e) {
    try {
      await backend().del(blobKey(local_attachment_id));
    } catch {
      /* ignore */
    }
    throw new CaptureOutboxError(
      "idb_unavailable",
      e instanceof Error ? e.message : "Could not save outbox metadata.",
    );
  }

  return meta;
}

async function loadBlob(meta: CaptureOutboxItemMeta): Promise<Blob | null> {
  if (meta.kind !== "audio") return null;
  try {
    const b = await backend().get(blobKey(meta.local_attachment_id));
    if (b instanceof Blob) return b;
    return null;
  } catch {
    return null;
  }
}

/**
 * Sync every **local_pending** or **failed** item (manual only — no background retry).
 * On full success, removes the local row and blob.
 */
export async function syncOutbox(client: ApiClient): Promise<{
  synced: number;
  failed: number;
  errors: string[];
}> {
  const errors: string[] = [];
  let synced = 0;
  let failed = 0;

  const items = await listOutboxItems();
  const work = items.filter(
    (m) => m.status === "local_pending" || m.status === "failed" || m.status === "syncing",
  );

  for (const meta of work) {
    const m: CaptureOutboxItemMeta = { ...meta, status: "syncing", last_error: null };
    await saveMeta(m);

    try {
      const text =
        m.kind === "audio"
          ? (m.text?.trim() || "(voice capture)")
          : m.kind === "text" || m.kind === "url"
            ? m.text?.trim() || undefined
            : undefined;
      const url =
        m.kind === "audio"
          ? (m.url?.trim() || undefined)
          : m.kind === "text" || m.kind === "url"
            ? m.url?.trim() || undefined
            : undefined;

      if (!text?.trim() && !url?.trim()) {
        throw new CaptureOutboxError("validation", "Cannot sync: missing text and URL.");
      }

      const created = await createCapture(client, {
        client_id: m.local_capture_id,
        title: m.title ?? null,
        text: text ?? null,
        url: url ?? null,
        tags: m.tags ?? null,
      });

      const captureId = created.capture_id;

      if (m.kind === "audio") {
        const blob = await loadBlob(m);
        if (!blob) {
          throw new Error("Local audio blob is missing; discard this item.");
        }
        const ext = m.mime_type.includes("webm") ? "webm" : m.mime_type.includes("mp4") ? "m4a" : "bin";
        const file = new File([blob], `sync.${ext}`, { type: m.mime_type });
        await uploadCaptureAttachment(client, captureId, file, m.local_attachment_id);
      }

      await discardOutboxItem(m.local_capture_id);
      synced += 1;
    } catch (e) {
      failed += 1;
      const msg =
        e instanceof ApiError
          ? formatCaptureErrorMessage(e.status, e.detail)
          : e instanceof CaptureOutboxError
            ? e.message
            : e instanceof Error
              ? e.message
              : "Sync failed.";
      errors.push(msg);
      const failMeta: CaptureOutboxItemMeta = { ...meta, status: "failed", last_error: msg };
      await saveMeta(failMeta);
    }
  }

  return { synced, failed, errors };
}

export function outboxErrorToMessage(err: unknown): string {
  if (err instanceof CaptureOutboxError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
