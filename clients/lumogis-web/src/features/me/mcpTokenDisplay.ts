// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Display helper for MCP token scope labels. Extracted from MeMcpTokensView so the
// component module exports only its component (react-refresh/only-export-components);
// shared by MeMcpTokensView and AdminMcpTokensView.

// Map a token's scopes to a human label. `null` = the v1 unrestricted default
// (legacy); a list containing `mcp:write` can write; otherwise read-only.
export function accessLabel(scopes: string[] | null): string {
  if (scopes === null) return "Unrestricted (legacy)";
  if (scopes.includes("mcp:write")) return "Read + write";
  return "Read-only";
}
