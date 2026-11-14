// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-837 — the client for the `/sources` collection.
//
// This module owns the two `POST /sources` modes and nothing else. The list,
// delete, sync and patch calls already live in `emailSources.ts` and are
// RE-EXPORTED here so a consumer has one import surface without a second copy
// of any of them existing. In particular there is exactly one wrapper for
// `PATCH /sources/{id}` — the widened `updateSource` — because a second one
// beside a live shipped call is how two divergent clients for one route start.
import type { ApiClient } from "./client";

export {
  listSources,
  deleteSource,
  syncSourceNow,
  updateSource,
  type SourcePatch,
  type UpdateSourceResponse,
  type SourceRow,
} from "./emailSources";

/** What `confirm=false` returns: what Lumogis detected, with nothing written. */
export interface SourcePreview {
  source_type: string;
  url: string;
  preview_items: { title?: string; url?: string }[];
  /** LUM-841 — `false` when the fetch itself failed (DNS, refused, timeout,
   *  non-2xx). NOT the same as "no feed found": a page that answered with
   *  nothing extractable is reachable with zero items, and is a perfectly good
   *  page source. Optional-safe against an older backend that does not send it,
   *  where `undefined` reads as "don't know" and the copy stays neutral. */
  reachable?: boolean;
}

export interface CreatedSource {
  status: string;
  source_id: string;
  source_type: string;
  url: string;
  preview_items: unknown[];
}

export interface CreateSourceBody {
  url: string;
  name?: string;
  category?: string;
  poll_interval?: number;
  source_type?: "paperless";
  acknowledge_external_consumer?: boolean;
  acknowledge_paperless_overlap?: boolean;
}

/**
 * Step one of the two-step flow: detect and preview, no DB write.
 *
 * For RSS and pages this performs a real fetch and returns up to three actual
 * items. For **paperless** it only normalises and SSRF-validates the base URL —
 * it never contacts the paperless instance and always returns an empty
 * `preview_items`, so a caller must not present it as a connection test.
 */
export function previewSource(client: ApiClient, body: CreateSourceBody): Promise<SourcePreview> {
  return client.postJson("/sources", { ...body, confirm: false });
}

/** Step two: detect, save, and schedule polling. */
export function createSource(client: ApiClient, body: CreateSourceBody): Promise<CreatedSource> {
  return client.postJson("/sources", { ...body, confirm: true });
}
