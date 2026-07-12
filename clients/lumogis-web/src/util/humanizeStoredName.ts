// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Derive a human filename from stored document names / paths (never show raw
// hash prefixes or workspace upload paths as primary titles).

const HASH_PREFIX_RE = /^[a-f0-9]{32}_/i;
const WORKSPACE_UPLOAD_RE = /\/workspace\/uploads\/[^/]+\//gi;

/** Strip hash prefixes and upload path segments; return basename for display. */
export function humanizeStoredName(
  storedName: string | null | undefined,
  filePath?: string | null,
): string {
  const raw = (storedName?.trim() || filePath?.trim() || "").replace(/\\/g, "/");
  if (!raw) return "Untitled document";

  let name = raw;
  if (name.includes("/")) {
    name = name.slice(name.lastIndexOf("/") + 1);
  }

  name = name.replace(WORKSPACE_UPLOAD_RE, "");
  name = name.replace(HASH_PREFIX_RE, "");

  return name.trim() || "Untitled document";
}

/** Full internal path/id string suitable for metadata captions (mono, muted). */
export function documentMetadataCaption(
  documentId: number | string,
  storedName?: string | null,
  filePath?: string | null,
): string {
  const path = filePath?.trim() || storedName?.trim() || "";
  if (path && String(documentId)) {
    return `#${documentId} · ${path}`;
  }
  return path || `#${documentId}`;
}
