// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import type { NavItem } from "./BottomNav";
import { Button } from "./Button";
import { NavIcon, isSecondaryNavKey, navIconKeyForItem } from "./NavIcons";
import {
  getSidebarCollapsed,
  setSidebarCollapsed,
} from "../design/sidebarCollapse";
import { useEffect, useState } from "react";

export interface SidebarNavProps {
  items: ReadonlyArray<NavItem>;
  activeKey?: string;
  onNavigate?: (key: string) => void;
  className?: string;
}

function NavLink({
  item,
  isActive,
  onNavigate,
  collapsed,
}: {
  item: NavItem;
  isActive: boolean;
  onNavigate?: (key: string) => void;
  collapsed: boolean;
}): JSX.Element {
  const iconKey = navIconKeyForItem(item.key);
  return (
    <a
      href={item.href}
      className="lumogis-sidebarnav__item"
      aria-current={isActive ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      onClick={(e) => {
        if (onNavigate !== undefined) {
          e.preventDefault();
          onNavigate(item.key);
        }
      }}
    >
      {iconKey ? (
        <span className="lumogis-sidebarnav__icon">
          <NavIcon name={iconKey} size={19} />
        </span>
      ) : null}
      <span className="lumogis-sidebarnav__label">{item.label}</span>
    </a>
  );
}

export function SidebarNav({
  items,
  activeKey,
  onNavigate,
  className,
}: SidebarNavProps): JSX.Element {
  const [collapsed, setCollapsed] = useState(() => getSidebarCollapsed());

  useEffect(() => {
    setSidebarCollapsed(collapsed);
  }, [collapsed]);

  const primary = items.filter((i) => !isSecondaryNavKey(i.key));
  const secondary = items.filter((i) => isSecondaryNavKey(i.key));

  return (
    <nav
      className={[className, collapsed ? "lumogis-sidebarnav--collapsed" : ""]
        .filter(Boolean)
        .join(" ")}
      aria-label="Primary navigation"
    >
      <div className="lumogis-sidebarnav__brand">
        <span className="lumogis-sidebarnav__wordmark">Lumogis</span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="lumogis-sidebarnav__collapse"
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? "»" : "«"}
        </Button>
      </div>
      <div className="lumogis-sidebarnav__group">
        {primary.map((item) => (
          <NavLink
            key={item.key}
            item={item}
            isActive={item.key === activeKey}
            onNavigate={onNavigate}
            collapsed={collapsed}
          />
        ))}
      </div>
      {secondary.length > 0 ? (
        <>
          <div className="lumogis-sidebarnav__spacer" aria-hidden />
          <div className="lumogis-sidebarnav__divider" aria-hidden />
          <div className="lumogis-sidebarnav__group">
            {secondary.map((item) => (
              <NavLink
                key={item.key}
                item={item}
                isActive={item.key === activeKey}
                onNavigate={onNavigate}
                collapsed={collapsed}
              />
            ))}
          </div>
        </>
      ) : null}
    </nav>
  );
}
