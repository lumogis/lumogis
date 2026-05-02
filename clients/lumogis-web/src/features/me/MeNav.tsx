// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { NavLink } from "react-router-dom";

const links: { to: string; label: string }[] = [
  { to: "/me/profile", label: "Profile" },
  { to: "/me/connectors", label: "Connectors" },
  { to: "/me/permissions", label: "Permissions" },
  { to: "/me/tools-capabilities", label: "Tools & capabilities" },
  { to: "/me/llm-providers", label: "LLM providers" },
  { to: "/me/mcp-tokens", label: "MCP tokens" },
  { to: "/me/notifications", label: "Notifications" },
  { to: "/me/export", label: "Export" },
];

export function MeNav(): JSX.Element {
  return (
    <nav className="lumogis-settings-nav" aria-label="Settings">
      {links.map((l) => (
        <NavLink key={l.to} to={l.to} className="lumogis-settings-nav__link">
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}
