// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Display helper for MCP token scope labels. Extracted from MeMcpTokensView so the
// component module exports only its component (react-refresh/only-export-components);
// shared by MeMcpTokensView and AdminMcpTokensView.

// Password-change consequence copy (LUM-885). A number is shown only when it is known and
// positive; zero, still-loading and failed lookups all use the generic sentence, so the copy
// never claims "0 tokens" about a list it could not read.
function tokenNoun(n: number): string {
  return `${n} MCP ${n === 1 ? "token" : "tokens"}`;
}

export function selfPasswordConsequence(active: number | null): string {
  if (active !== null && active > 0) {
    return `Saving signs you out on every device and revokes your ${tokenNoun(active)}. MCP clients such as Claude Desktop or the lumogis-mcp bridge will need a new token.`;
  }
  return "Saving signs you out on every device and revokes any MCP tokens you hold.";
}

export function adminResetConsequence(email: string, active: number | null): string {
  if (active !== null && active > 0) {
    return `Saving signs ${email} out on every device and revokes their ${tokenNoun(active)}.`;
  }
  return `Saving signs ${email} out on every device and revokes any MCP tokens they hold.`;
}

// Map a token's scopes to a human label. `null` = the v1 unrestricted default
// (legacy); a list containing `mcp:write` can write; otherwise read-only.
export function accessLabel(scopes: string[] | null): string {
  if (scopes === null) return "Unrestricted (legacy)";
  if (scopes.includes("mcp:write")) return "Read + write";
  return "Read-only";
}
