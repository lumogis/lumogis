// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The Settings frame (LUM-993 Pass 3, Decisions 5 and 6). A container query on
// this wrapper, not the viewport: when the Settings area is at least 720 px
// wide, a 240 px menu sits beside the page; narrower, /settings shows the
// grouped index and each page shows a "Settings" back link instead.
// Advanced is collapsible; For developers appears only for admins with
// "Show developer tools" on (the switch sits at the foot of the menu).

import { ChevronDown, ChevronLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useMatch } from "react-router-dom";

import { useUser } from "../../auth/AuthProvider";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { useDevToolsShown } from "../../design/devTools";
import { cn } from "@/lib/utils";
import { DevToolsSwitch } from "./DevToolsSwitch";
import { listedGroups, type SettingsEntry, type Viewer } from "./settingsNav";

function MenuItem({ entry }: { entry: SettingsEntry }): JSX.Element {
  const Icon = entry.icon;
  return (
    <NavLink
      to={`/settings/${entry.slug}`}
      className={({ isActive }) =>
        cn(
          "flex min-h-9 items-center gap-2.5 rounded-md px-2.5 text-sm no-underline outline-none focus-visible:ring-3 focus-visible:ring-ring/50 pointer-coarse:min-h-[44px]",
          isActive
            ? "bg-accent-soft font-medium text-accent-text hover:text-accent-text"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )
      }
    >
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span className="min-w-0 truncate">{entry.title}</span>
    </NavLink>
  );
}

function GroupHeading({ children, id }: { children: string; id: string }): JSX.Element {
  return (
    <h2 id={id} className="m-0 px-2.5 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
      {children}
    </h2>
  );
}

export function SettingsLayout(): JSX.Element {
  const user = useUser();
  const [devTools] = useDevToolsShown();
  const viewer: Viewer = { isAdmin: user?.role === "admin", devTools };
  const onPage = useMatch("/settings/:slug") !== null;
  const { pathname } = useLocation();
  const groups = listedGroups(viewer);
  // Advanced starts closed, and opens whenever the current page is in it (also
  // when you get there from the index, while this layout stays mounted).
  const inCollapsible = groups.some(
    (g) => g.group.collapsible && g.entries.some((e) => pathname === `/settings/${e.slug}` || pathname.startsWith(`/settings/${e.slug}/`)),
  );
  const [advancedOpen, setAdvancedOpen] = useState(inCollapsible);
  useEffect(() => {
    if (inCollapsible) setAdvancedOpen(true);
  }, [inCollapsible]);

  return (
    <div className="@container/settings" data-testid="settings-layout">
      <div className="flex flex-col gap-6 @min-[720px]/settings:grid @min-[720px]/settings:grid-cols-[240px_minmax(0,1fr)] @min-[720px]/settings:gap-8">
        <nav aria-label="Settings" className="hidden flex-col gap-4 @min-[720px]/settings:flex">
          {groups.map(({ group, entries }) =>
            group.collapsible ? (
              <Collapsible key={group.id} open={advancedOpen} onOpenChange={setAdvancedOpen}>
                <CollapsibleTrigger className="flex w-full items-center justify-between rounded-md px-2.5 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
                  {group.title}
                  <ChevronDown className={cn("size-3.5 transition-transform", advancedOpen && "rotate-180")} aria-hidden="true" />
                </CollapsibleTrigger>
                <CollapsibleContent className="flex flex-col gap-0.5">
                  {entries.map((e) => (
                    <MenuItem key={e.slug} entry={e} />
                  ))}
                </CollapsibleContent>
              </Collapsible>
            ) : (
              <section key={group.id} aria-labelledby={`settings-nav-${group.id}`} className="flex flex-col gap-0.5">
                <GroupHeading id={`settings-nav-${group.id}`}>{group.title}</GroupHeading>
                {entries.map((e) => (
                  <MenuItem key={e.slug} entry={e} />
                ))}
              </section>
            ),
          )}
          {viewer.isAdmin ? <DevToolsSwitch className="border-t border-border px-2.5 pt-3" /> : null}
        </nav>

        <div className="min-w-0 max-w-3xl">
          {onPage ? (
            <Link
              to="/settings"
              className="mb-3 inline-flex items-center gap-1 text-sm text-accent-text no-underline @min-[720px]/settings:hidden pointer-coarse:min-h-[44px]"
            >
              <ChevronLeft className="size-4" aria-hidden="true" />
              Settings
            </Link>
          ) : null}
          <Outlet context={viewer} />
        </div>
      </div>
    </div>
  );
}
