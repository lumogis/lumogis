// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// One Settings page, /settings/:slug (LUM-993 Pass 3). It finds the page in
// settingsNav, applies the gates, renders the one PageHeader (title, purpose
// line, (i), and the Mine / Household switch on scoped pages), then the view.
//
// Gates (Decision 3): an unknown page opens Profile, no toast. A member on an
// admin or developer page is sent to Profile with "Only household admins can
// open that page." A member asking for the Household view of a scoped page
// sees Mine, with "Only household admins can see everyone's." Developer pages
// open from a link for an admin even with the switch off, with a notice.

import { useEffect, useRef } from "react";
import { Navigate, useOutletContext, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { useUser } from "../../auth/AuthProvider";
import { PageHeader } from "../../components/page/PageHeader";
import { ScopeSwitch, type Scope } from "../../components/page/ScopeSwitch";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { help } from "../../copy/help";
import { useDevToolsShown } from "../../design/devTools";
import { ADMIN_ONLY_TOAST, HOUSEHOLD_VIEW_TOAST, SettingsHeadingContext } from "./settingsHeading";
import { entryBySlug, mayOpen, type Viewer } from "./settingsNav";

export function SettingsPage(): JSX.Element {
  const { slug } = useParams();
  const entry = entryBySlug(slug);
  const viewer = useOutletContext<Viewer>();
  const user = useUser();
  const [params, setParams] = useSearchParams();
  const [, setDevTools] = useDevToolsShown();
  const headingRef = useRef<HTMLHeadingElement>(null);

  const scoped = entry?.scope !== undefined;
  const wantsHousehold = params.get("view") === "household";
  const memberAskedHousehold = Boolean(user) && scoped && wantsHousehold && !viewer.isAdmin;

  // Once per navigation (StrictMode runs effects twice in development).
  const toastedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!memberAskedHousehold) return;
    const key = `${slug}?${params.toString()}`;
    if (toastedFor.current === key) return;
    toastedFor.current = key;
    toast(HOUSEHOLD_VIEW_TOAST);
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("view");
        return next;
      },
      { replace: true },
    );
  }, [memberAskedHousehold, setParams, slug, params]);

  if (!entry) return <Navigate to="/settings/profile" replace />;
  if (!user) return <p role="status">Loading…</p>;
  if (!mayOpen(entry, viewer)) {
    return <Navigate to="/settings/profile" replace state={{ toast: ADMIN_ONLY_TOAST }} />;
  }

  const copy = help[entry.helpKey] as { page?: string; about?: string };
  const household = scoped && wantsHousehold && viewer.isAdmin;
  const View = entry.scope ? (household ? entry.scope.household : entry.scope.mine) : entry.Component;

  const switchView = (next: Scope): void => {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next === "household") p.set("view", "household");
        else p.delete("view");
        // Decision 12: paging belongs to one view; shared filters stay.
        p.delete("offset");
        return p;
      },
      { replace: true },
    );
  };

  return (
    <SettingsHeadingContext.Provider value={headingRef}>
      {entry.audience === "developer" && !viewer.devTools ? (
        <Alert className="mb-4">
          <AlertDescription className="flex flex-wrap items-center gap-2">
            <span>Developer tools are hidden in the menu.</span>
            <Button variant="outline" size="xs" onClick={() => setDevTools(true)}>
              Show them
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      <PageHeader
        title={entry.title}
        description={copy.page}
        info={copy.about}
        headingRef={headingRef}
        headingTabIndex={-1}
        action={entry.scope ? <ScopeSwitch value={household ? "household" : "mine"} onChange={switchView} /> : undefined}
      />
      {/* key: a different view is a different page (state does not leak between Mine and Household). */}
      <View key={`${entry.slug}:${household ? "household" : "mine"}`} />
    </SettingsHeadingContext.Provider>
  );
}
