// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only GET /api/v1/admin/diagnostics/backup-status.

import type { ApiClient } from "./client";

export interface BackupStatusStoreItem {
  id: "postgres" | "qdrant" | "falkordb" | "files";
  present: boolean;
  skipped: boolean;
  skip_reason: string | null;
  /** files store only (LUM-892) */
  file_count?: number | null;
  bytes?: number | null;
}

/** One line of "what the DR backup covers" (LUM-892). */
export interface BackupCoverageItem {
  id: string;
  label: string;
  covered: boolean;
  note?: string | null;
}

export interface BackupStatusWarning {
  code: string;
  message: string;
}

export interface BackupStatusResponse {
  enabled: boolean;
  backup_dir: string;
  last_snapshot_id: string | null;
  last_success_at: string | null;
  age_hours: number | null;
  stale: boolean;
  stale_threshold_hours: number;
  total_bytes: number | null;
  stores: BackupStatusStoreItem[];
  last_verify_status: "ok" | "failed" | "unknown" | null;
  warnings: BackupStatusWarning[];
  /**
   * LUM-892. Optional: a Core without file backup omits all three keys, so the
   * view must read `coverage ?? []`, never `coverage.length`.
   */
  coverage?: BackupCoverageItem[];
  /** Uploaded documents and capture files not on disk; null = could not count. */
  files_missing?: number | null;
  files_missing_by_area?: Record<string, number> | null;
}

export function fetchAdminBackupStatus(client: ApiClient): Promise<BackupStatusResponse> {
  return client.getJson<BackupStatusResponse>("/api/v1/admin/diagnostics/backup-status");
}
