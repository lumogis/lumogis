// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// /settings with no page (LUM-993 Decision 5): the grouped list of every page
// this person may see, each with its purpose line. On a phone it is the start
// page; on a wide screen it fills the content pane beside the menu. It ends with
// Sign out: when Settings is a phone tab of its own there is no More sheet to
// hold it (LUM-993 follow-up).

import { ChevronRight, LogOut } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";

import { useAuth, useUser } from "../../auth/AuthProvider";
import { PageHeader } from "../../components/page/PageHeader";
import { Button } from "../../components/ui/button";
import { help } from "../../copy/help";
import { DevToolsSwitch } from "./DevToolsSwitch";
import { listedGroups, type Viewer } from "./settingsNav";

export function SettingsHome({ viewer }: { viewer: Viewer }): JSX.Element {
  const user = useUser();
  const { logout } = useAuth();
  return (
    <div className="flex flex-col gap-6" data-testid="settings-home">
      <PageHeader title="Settings" description={help.settings.page} />
      {listedGroups(viewer).map(({ group, entries }) => (
        <section key={group.id} aria-labelledby={`settings-home-${group.id}`} className="flex flex-col gap-2">
          <h2 id={`settings-home-${group.id}`} className="m-0 px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {group.title}
          </h2>
          <ul className="m-0 flex list-none flex-col divide-y divide-border overflow-hidden rounded-xl border border-border bg-card p-0" role="list">
            {entries.map((e) => {
              const Icon = e.icon;
              const page = (help[e.helpKey] as { page?: string }).page;
              return (
                <li key={e.slug}>
                  <Link
                    to={`/settings/${e.slug}`}
                    className="flex min-h-14 items-center gap-3 px-4 py-2.5 text-foreground no-underline outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset"
                  >
                    <Icon className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="text-sm font-medium">{e.title}</span>
                      {page ? <span className="truncate text-xs text-muted-foreground">{page}</span> : null}
                    </span>
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {viewer.isAdmin ? <DevToolsSwitch className="px-1" /> : null}
      {user ? (
        <section aria-label="Account" className="flex flex-col gap-2">
          <div className="flex min-h-14 items-center gap-3 rounded-xl border border-border bg-card px-4 py-2.5">
            <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{user.email}</span>
            <Button type="button" variant="ghost" onClick={() => void logout()} className="min-h-11 text-destructive hover:bg-destructive/10 hover:text-destructive">
              <LogOut aria-hidden="true" />
              Sign out
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}

/** The /settings index route (reads the viewer from SettingsLayout). */
export function SettingsIndex(): JSX.Element {
  return <SettingsHome viewer={useOutletContext<Viewer>()} />;
}
