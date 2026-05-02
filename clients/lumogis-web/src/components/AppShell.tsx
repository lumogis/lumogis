// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// AppShell — parent plan §"Phase 1 Pass 1.1 item 5".
//
// Container-query driven responsive layout: at compact (< 720px) the
// BottomNav renders; at regular (≥ 720px) the SidebarNav renders.
// Phase 3E adds `OfflineBanner` inside `lumogis-shell__top` below the header.
// Container queries (rather than viewport @media) let the shell embed inside any
// surface (e.g. a future split-pane or admin overlay) and respond to its
// allotted width, not the window's.

import type { ReactNode } from "react";

import { useUser, useAuth } from "../auth/AuthProvider";
import { ThemeToggle } from "./ThemeToggle";
import { BottomNav, NAV_ITEMS, type NavItem } from "./BottomNav";
import { SidebarNav } from "./SidebarNav";
import { OfflineBanner } from "./OfflineBanner";
import { useOnlineStatus } from "../pwa/useOnlineStatus";

export interface AppShellProps {
  children: ReactNode;
  /** Override the default nav (mostly for tests). */
  navItems?: ReadonlyArray<NavItem>;
  /** Currently active nav item key (Pass 1.2+ wires this from the router). */
  activeKey?: string;
  /** Called when the user picks a nav item (Pass 1.2+ wires to navigate()). */
  onNavigate?: (key: string) => void;
}

export function AppShell({
  children,
  navItems = NAV_ITEMS,
  activeKey,
  onNavigate,
}: AppShellProps): JSX.Element {
  const user = useUser();
  const { logout } = useAuth();
  const online = useOnlineStatus();

  return (
    <div className="lumogis-shell" data-testid="lumogis-shell">
      <div className="lumogis-shell__top">
        <header className="lumogis-shell__header">
          <span>Lumogis</span>
          <span className="lumogis-shell__header-tools">
            <ThemeToggle />
            {user && (
              <>
                <span className="lumogis-shell__user-email" aria-label="signed-in user" title={user.email}>
                  {user.email}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    void logout();
                  }}
                  style={{
                    background: "transparent",
                    border: 0,
                    color: "inherit",
                    cursor: "pointer",
                    font: "inherit",
                    padding: "0.5rem 0.75rem",
                  }}
                >
                  Sign out
                </button>
              </>
            )}
          </span>
        </header>
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
