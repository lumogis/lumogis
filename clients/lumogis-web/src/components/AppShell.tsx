// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// AppShell — container-query driven responsive layout.

import type { ReactNode } from "react";

import { BottomNav, NAV_ITEMS, type NavItem } from "./BottomNav";
import { OfflineBanner } from "./OfflineBanner";
import { SingleUserBanner } from "./SingleUserBanner";
import { ProfileMenuButton } from "./ProfileMenu";
import { SidebarNav } from "./SidebarNav";
import { useAuth } from "../auth/AuthProvider";
import { useOnlineStatus } from "../pwa/useOnlineStatus";

export interface AppShellProps {
  children: ReactNode;
  navItems?: ReadonlyArray<NavItem>;
  activeKey?: string;
  onNavigate?: (key: string) => void;
}

export function AppShell({
  children,
  navItems = NAV_ITEMS,
  activeKey,
  onNavigate,
}: AppShellProps): JSX.Element {
  const online = useOnlineStatus();
  const { singleUser } = useAuth();

  return (
    <div className="lumogis-shell" data-testid="lumogis-shell">
      <div className="lumogis-shell__top">
        <header className="lumogis-shell__header">
          <span className="lumogis-shell__header-title">Lumogis</span>
          <span className="lumogis-shell__header-tools">
            <ProfileMenuButton />
          </span>
        </header>
        <SingleUserBanner visible={singleUser} />
        <OfflineBanner visible={!online} />
      </div>

      <div className="lumogis-shell__body">
        <SidebarNav
          className="lumogis-shell__sidebar"
          items={navItems}
          activeKey={activeKey}
          onNavigate={onNavigate}
        />
        <main className="lumogis-shell__main" id="lumogis-main">
          {children}
        </main>
      </div>

      <BottomNav
        className="lumogis-shell__bottom"
        items={navItems}
        activeKey={activeKey}
        onNavigate={onNavigate}
      />
    </div>
  );
}
