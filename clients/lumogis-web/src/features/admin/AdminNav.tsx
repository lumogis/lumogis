// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { NavLink } from "react-router-dom";

const mainLinks: { to: string; label: string }[] = [
  { to: "/admin/users", label: "Users" },
  { to: "/admin/connector-credentials", label: "Connector credentials" },
  { to: "/admin/connector-permissions", label: "Connector permissions" },
  { to: "/admin/mcp-tokens", label: "MCP tokens" },
  { to: "/admin/audit", label: "Audit" },
  { to: "/admin/diagnostics", label: "Diagnostics" },
];

const legacy: { href: string; label: string }[] = [
  { href: "/settings", label: "Settings & ollama" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/graph/mgm", label: "KG management" },
  { href: "/review-queue", label: "Review queue" },
  { href: "/backup", label: "Backup/restore" },
  { href: "/kg/stop-entities", label: "Stop entities" },
  { href: "/health", label: "Stack health" },
];

export function AdminNav(): JSX.Element {
  return (
    <div className="lumogis-admin-nav-root">
      <nav className="lumogis-settings-nav lumogis-admin-nav__main" aria-label="Administration">
        {mainLinks.map((l) => (
          <NavLink key={l.to} to={l.to} className="lumogis-settings-nav__link">
            {l.label}
          </NavLink>
        ))}
      </nav>
      <div className="lumogis-admin-nav__legacy">
        <div style={{ opacity: 0.8, marginBottom: "0.35rem" }}>Legacy admin tools</div>
        <ul>
          {legacy.map((l) => (
            <li key={l.href}>
              <a href={l.href} target="_blank" rel="noopener noreferrer">
                {l.label} <span style={{ fontSize: "0.7rem" }}>(opens legacy dashboard)</span>
              </a>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
