// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// MCP token row types, shared by the member and admin token views and by the
// password-change consequence copy (LUM-885). Mirrors the API models
// `McpTokenPublic` / `McpTokenAdminView` (`orchestrator/models/mcp_token.py`).

export interface McpTokenRow {
  id: string;
  label: string;
  created_at: string;
  scopes: string[] | null;
  last_used_at?: string | null;
  expires_at?: string | null;
  // The admin list includes revoked rows; the member list returns active ones only.
  revoked_at?: string | null;
}

export interface McpTokenAdminRow extends McpTokenRow {
  user_id: string;
}
