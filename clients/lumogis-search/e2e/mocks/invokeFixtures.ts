// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/** Mock IPC payloads — camelCase on the wire (matches Rust Serialize DTOs). */

export type OverlaySettingsPayload = {
  schemaVersion: number;
  orchestratorBaseUrl: string;
  hotkey: string;
  libraryRoots: string[];
  theme: string;
  keychainError?: string | null;
  authMode: string;
  sessionPresent: boolean;
  sessionRole?: string | null;
  onboardingComplete: boolean;
};

export type AuthProbeResult = {
  mode: "on" | "off" | "unreachable" | "unknown";
  sessionPresent: boolean;
  role?: string | null;
};

export type AuthSessionPublic = {
  role: string;
  expiresAtUnix: number;
  present: boolean;
};

export type AdminSettingsPublic = {
  ingestPaths: string[];
  pendingIngestPaths?: string[] | null;
  restartRequired: boolean;
  paperlessConfigured: boolean;
};

export type MemorySearchHitDto = {
  id: string;
  score: number;
  snippet: string;
  scope: string;
  title?: string | null;
  source?: string | null;
  created_at?: string | null;
};

export type MemorySearchResponseDto = {
  hits: MemorySearchHitDto[];
  degraded: boolean;
  reason?: string | null;
};

export type IngestUploadQueuedPublic = {
  status: string;
  fileId: string;
};

const BASE_SETTINGS: OverlaySettingsPayload = {
  schemaVersion: 3,
  orchestratorBaseUrl: "http://127.0.0.1:8000",
  hotkey: "CommandOrControl+Shift+L",
  libraryRoots: [],
  theme: "system",
  keychainError: null,
  authMode: "on",
  sessionPresent: false,
  sessionRole: null,
  onboardingComplete: true,
};

export function loggedOutSettings(
  over: Partial<OverlaySettingsPayload> = {},
): OverlaySettingsPayload {
  return {
    ...BASE_SETTINGS,
    authMode: "on",
    sessionPresent: false,
    sessionRole: null,
    ...over,
  };
}

export function loggedInSettings(
  role: string,
  over: Partial<OverlaySettingsPayload> = {},
): OverlaySettingsPayload {
  return {
    ...BASE_SETTINGS,
    authMode: "on",
    sessionPresent: true,
    sessionRole: role,
    ...over,
  };
}

export function loggedOutProbe(): AuthProbeResult {
  return { mode: "on", sessionPresent: false };
}

export function loggedInProbe(role: string): AuthProbeResult {
  return { mode: "on", sessionPresent: true, role };
}

export function validLoginSession(): AuthSessionPublic {
  return { role: "admin", expiresAtUnix: Math.floor(Date.now() / 1000) + 3600, present: true };
}

export function memberLoginSession(): AuthSessionPublic {
  return { role: "member", expiresAtUnix: Math.floor(Date.now() / 1000) + 3600, present: true };
}

export function defaultAdminSettings(
  over: Partial<AdminSettingsPublic> = {},
): AdminSettingsPublic {
  return {
    ingestPaths: ["/data/ingest"],
    pendingIngestPaths: null,
    restartRequired: false,
    paperlessConfigured: false,
    ...over,
  };
}

export function sampleSearchHit(snippet = "health check snippet"): MemorySearchResponseDto {
  return {
    hits: [
      {
        id: "hit-1",
        score: 0.92,
        snippet,
        scope: "personal",
      },
    ],
    degraded: false,
  };
}

export function queuedUpload(): IngestUploadQueuedPublic {
  return { status: "queued", fileId: "file-test-001" };
}
