// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// An old Settings address (/me/..., /admin/..., /audit) opening its new page
// (LUM-993 Pass 3). The old query string survives (the target's own query wins
// on the same key) and so does router state (e.g. `fromOnboarding`, Decision 11).

import { Navigate, useLocation } from "react-router-dom";

import { mergeQuery } from "./legacyQuery";

export function LegacyRedirect({ to }: { to: string }): JSX.Element {
  const loc = useLocation();
  // An old personal address (/me/...) never asks for the household view.
  const search = new URLSearchParams(loc.search);
  if (loc.pathname === "/me" || loc.pathname.startsWith("/me/")) search.delete("view");
  const query = search.toString();
  return <Navigate replace to={mergeQuery(to, query ? `?${query}` : "") + loc.hash} state={loc.state} />;
}
