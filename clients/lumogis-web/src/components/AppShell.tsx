// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// AppShell (LUM-993 Pass 2a): no header bar. Wide screens get the sidebar
// (AppSidebar), phones the tab bar (MobileNav); the switch is a container query
// at 720 px on `.lumogis-shell` (tokens.css). The legacy `lumogis-shell__*`
// classes sit on wrappers and only decide which of the two shows: a Tailwind
// display class on the nav itself would beat their `display: none`.

import type { ReactNode } from "react";

import { AppSidebar } from "./shell/AppSidebar";
import { MobileNav } from "./shell/MobileNav";
import { ShellBanners } from "./shell/ShellBanners";
import type { NavKey, ShellNavLists } from "./shell/navItems";
import { TooltipProvider } from "./ui/tooltip";

export type ShellNav = ShellNavLists;

export interface AppShellProps {
  children: ReactNode;
  nav: ShellNav;
  activeKey: NavKey;
  onNavigate: (href: string) => void;
}

export function AppShell({ children, nav, activeKey, onNavigate }: AppShellProps): JSX.Element {
  return (
    <TooltipProvider>
      <div className="lumogis-shell" data-testid="lumogis-shell">
        <div className="lumogis-shell__body">
          <div className="lumogis-shell__sidebar">
            <AppSidebar
              className="h-full"
              primary={nav.primary}
              secondary={nav.secondary}
              activeKey={activeKey}
              onNavigate={onNavigate}
              pendingApprovals={nav.pendingApprovals}
            />
          </div>
          <main className="lumogis-shell__main" id="lumogis-main">
            <ShellBanners />
            {children}
          </main>
        </div>
        <div className="lumogis-shell__bottom">
          <MobileNav
            className="w-full"
            tabs={nav.tabs}
            more={nav.more}
            activeKey={activeKey}
            onNavigate={onNavigate}
            pendingApprovals={nav.pendingApprovals}
          />
        </div>
      </div>
    </TooltipProvider>
  );
}
