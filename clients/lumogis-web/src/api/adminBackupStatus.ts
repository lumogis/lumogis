// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only GET /api/v1/admin/diagnostics/backup-status.

import type { ApiClient } from "./client";

export interface BackupStatusStoreItem {
  id: "postgres" | "qdrant" | "falkordb";
  present: boolean;
  skipped: boolean;
  skip_reason: string | null;
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
}

export function fetchAdminBackupStatus(client: ApiClient): Promise<BackupStatusResponse> {
  return client.getJson<BackupStatusResponse>("/api/v1/admin/diagnostics/backup-status");
}
