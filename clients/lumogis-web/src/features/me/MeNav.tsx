// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { NavLink } from "react-router-dom";

const links: { to: string; label: string }[] = [
  { to: "/me/profile", label: "Profile" },
  { to: "/me/appearance", label: "Appearance" },
  { to: "/me/connectors", label: "Connectors" },
  { to: "/me/sources", label: "Sources" },
  { to: "/me/email-signals", label: "Email signals" },
  { to: "/me/email-labels", label: "Email labels" },
  { to: "/me/relevance", label: "Relevance tuning" },
  // LUM-849: "Permissions" reads like access control — who can see what. This
  // page is autonomy: what may Lumogis do on your behalf, and does it ask first.
  // The label is most of what makes it findable for the question people have.
  // The PATH deliberately stays `/me/permissions`: renaming a label is
  // reversible, breaking a bookmarked security page is not.
  { to: "/me/permissions", label: "What Lumogis may do" },
  { to: "/me/tools-capabilities", label: "Tools & capabilities" },
  { to: "/me/llm-providers", label: "LLM providers" },
  { to: "/me/mcp-tokens", label: "MCP tokens" },
  { to: "/me/shared-items", label: "My shared items" },
  { to: "/me/notifications", label: "Notifications" },
  { to: "/me/privacy-mode", label: "Privacy mode" },
  { to: "/me/export", label: "Export" },
  { to: "/audit", label: "My activity" },
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
