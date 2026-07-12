// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { NavLink } from "react-router-dom";

const mainLinks: { to: string; label: string }[] = [
  { to: "/admin/users", label: "Users" },
  { to: "/admin/connector-credentials", label: "Connector credentials" },
  { to: "/admin/connector-permissions", label: "Connector permissions" },
  { to: "/admin/mcp-tokens", label: "MCP tokens" },
  { to: "/admin/shared-items", label: "Household shared items" },
  { to: "/admin/audit", label: "Household audit" },
  { to: "/admin/diagnostics", label: "Diagnostics" },
  { to: "/admin/system-status", label: "System status" },
  { to: "/admin/privacy-mode", label: "Privacy mode" },
  { to: "/admin/search-settings", label: "Search & retrieval" },
  { to: "/admin/feature-flags", label: "Feature flags" },
];

export function AdminNav(): JSX.Element {
  return (
    <nav className="lumogis-settings-nav lumogis-admin-nav__main" aria-label="Administration">
      {mainLinks.map((l) => (
        <NavLink key={l.to} to={l.to} className="lumogis-settings-nav__link">
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}
