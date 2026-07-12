// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// BottomNav (compact mode) — hidden via container query at ≥ 720px.

/* eslint-disable react-refresh/only-export-components */

import { NavIcon, navIconKeyForItem } from "./NavIcons";

export interface NavItem {
  key: string;
  label: string;
  href: string;
}

export const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { key: "chat", label: "Chat", href: "/chat" },
  { key: "search", label: "Search", href: "/search" },
  { key: "documents", label: "Library", href: "/documents" },
  { key: "capture", label: "Capture", href: "/capture" },
  { key: "approvals", label: "Approvals", href: "/approvals" },
];

export interface BottomNavProps {
  items: ReadonlyArray<NavItem>;
  activeKey?: string;
  onNavigate?: (key: string) => void;
  className?: string;
}

export function BottomNav({
  items,
  activeKey,
  onNavigate,
  className,
}: BottomNavProps): JSX.Element {
  return (
    <nav className={className} aria-label="Primary navigation">
      {items.map((item) => {
        const isActive = item.key === activeKey;
        const iconKey = navIconKeyForItem(item.key);
        return (
          <a
            key={item.key}
            href={item.href}
            className="lumogis-bottomnav__item"
            aria-current={isActive ? "page" : undefined}
            onClick={(e) => {
              if (onNavigate !== undefined) {
                e.preventDefault();
                onNavigate(item.key);
              }
            }}
          >
            {iconKey ? (
              <span className="lumogis-bottomnav__icon">
                <NavIcon name={iconKey} size={20} />
              </span>
            ) : null}
            <span className="lumogis-bottomnav__label">{item.label}</span>
          </a>
        );
      })}
    </nav>
  );
}
