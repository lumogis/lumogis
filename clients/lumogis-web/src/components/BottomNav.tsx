// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// BottomNav (compact mode) — parent plan §"Phase 1 Pass 1.1 item 5".
// Hidden via container query at ≥ 720px (see tokens.css).
//
// We co-locate `NavItem` + `NAV_ITEMS` with the BottomNav component so the
// nav schema is owned by the component that introduced it. The
// react-refresh `only-export-components` rule penalises this slightly for
// HMR; that's a DX-only concern, so we silence it here.
/* eslint-disable react-refresh/only-export-components */

export interface NavItem {
  key: string;
  label: string;
  /** Path the future router will navigate to (Pass 1.2+). */
  href: string;
}

export const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { key: "chat", label: "Chat", href: "/chat" },
  { key: "search", label: "Search", href: "/search" },
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
            {item.label}
          </a>
        );
      })}
    </nav>
  );
}
