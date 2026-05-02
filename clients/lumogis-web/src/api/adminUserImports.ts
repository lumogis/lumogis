// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { ApiClient, ApiError } from "./client";
import { parseFilenameFromContentDisposition } from "../util/contentDisposition";

/** Inventory row from `GET /api/v1/admin/user-imports`. */
export interface ArchiveInventoryEntry {
  user_id: string;
  archive_filename: string;
  bytes: number;
  mtime: string;
  manifest_status: "valid" | "unparseable" | "missing_manifest" | "unsupported_version";
  manifest_version: number | null;
  exported_user_email: string | null;
}

export interface ImportPreconditions {
  archive_integrity_ok: boolean;
  manifest_present: boolean;
  manifest_parses: boolean;
  manifest_version_supported: boolean;
  target_email_available: boolean;
  all_required_sections_present: boolean;
  no_parent_pk_collisions: boolean;
}

export interface SectionSummary {
  name: string;
  kind: "postgres" | "qdrant" | "falkordb" | "user_record";
  row_count: number;
}

export interface ImportPlan {
  manifest_version: number;
  scope_filter: string;
  falkordb_edge_policy: string;
  exported_user: Record<string, unknown>;
  sections: SectionSummary[];
  missing_sections: string[];
  dangling_references: { section: string; field: string; count: number; sample_values: string[] }[];
  falkordb_external_edge_count: number;
  preconditions: ImportPreconditions;
  would_succeed: boolean;
  warnings: string[];
}

export interface ImportReceipt {
  new_user_id: string;
  archive_filename: string;
  sections_imported: SectionSummary[];
  qdrant_zero_vector_count?: number;
  falkordb_nodes_imported?: number;
  falkordb_edges_imported?: number;
  falkordb_external_edges_skipped?: number;
  leaf_pk_collisions_per_table?: Record<string, number>;
  warnings?: string[];
}

export interface ImportRequestBody {
  archive_path: string;
  new_user: { email: string; password: string; role: "admin" | "user" };
  dry_run: boolean;
}

export type UserImportResult =
  | { kind: "plan"; plan: ImportPlan }
  | { kind: "receipt"; receipt: ImportReceipt; location: string | null };

/** Relative path accepted by `POST /api/v1/admin/user-imports` for a listed archive. */
export function archivePathForInventoryEntry(entry: ArchiveInventoryEntry): string {
  return `${entry.user_id}/${entry.archive_filename}`;
}

export async function listUserImportArchives(client: ApiClient): Promise<ArchiveInventoryEntry[]> {
  return client.getJson<ArchiveInventoryEntry[]>("/api/v1/admin/user-imports");
}

async function readHttpErrorMessage(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail && typeof body.detail === "object" && body.detail !== null) {
      const d = body.detail as { refusal_reason?: string; payload?: unknown };
      if (typeof d.refusal_reason === "string") {
        const p = d.payload !== undefined ? ` — ${JSON.stringify(d.payload)}` : "";
        return `${d.refusal_reason}${p}`;
      }
      return JSON.stringify(body.detail);
    }
  } catch {
    /* use raw text */
  }
  return text.length > 0 ? text : res.statusText || "request failed";
}

export async function postUserImport(client: ApiClient, body: ImportRequestBody): Promise<UserImportResult> {
  const res = await client.fetch("/api/v1/admin/user-imports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await readHttpErrorMessage(res));
  }
  const parsed = (await res.json()) as ImportPlan | ImportReceipt;
  if (res.status === 201 && "new_user_id" in parsed) {
    return {
      kind: "receipt",
      receipt: parsed as ImportReceipt,
      location: res.headers.get("Location"),
    };
  }
  return { kind: "plan", plan: parsed as ImportPlan };
}

/**
 * Admin export of another user’s per-user backup ZIP (`POST /api/v1/me/export` + `target_user_id`).
 * Uses `fetchOnce` so failed streams are not auto-retried.
 */
export async function downloadAdminUserExportZip(
  client: ApiClient,
  targetUserId: string,
): Promise<{ blob: Blob; filename: string }> {
  const res = await client.fetchOnce("/api/v1/me/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_user_id: targetUserId }),
  });
  if (res.status === 413) {
    throw new ApiError(413, "Archive too large for this server limit.");
  }
  if (res.status === 401) {
    throw new ApiError(401, "Session expired; please refresh and try again.");
  }
  if (!res.ok) {
    throw new ApiError(res.status, await readHttpErrorMessage(res));
  }
  const blob = await res.blob();
  const filename =
    parseFilenameFromContentDisposition(res.headers.get("Content-Disposition")) ?? "export.zip";
  return { blob, filename };
}
