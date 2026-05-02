// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// SidebarNav (regular mode) — parent plan §"Phase 1 Pass 1.1 item 5".
// Hidden via container query at < 720px (see tokens.css).

import type { NavItem } from "./BottomNav";

export interface SidebarNavProps {
  items: ReadonlyArray<NavItem>;
  activeKey?: string;
  onNavigate?: (key: string) => void;
  className?: string;
}

export function SidebarNav({
  items,
  activeKey,
  onNavigate,
  className,
}: SidebarNavProps): JSX.Element {
  return (
    <nav className={className} aria-label="Primary navigation">
      {items.map((item) => {
        const isActive = item.key === activeKey;
        return (
          <a
            key={item.key}
            href={item.href}
            className="lumogis-sidebarnav__item"
            aria-current={isActive ? "page" : undefined}
            onClick={(e) => {
              if (onNavigate !== undefined) {
                e.preventDefault();
                onNavigate(item.key);
              }
            }}
          >
            {item.label}
          </a>
        );
      })}
    </nav>
  );
}
