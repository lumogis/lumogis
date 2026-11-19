// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Route-state messages (`navigate(to, { state: { toast } })`, e.g. Settings'
// "Only household admins can open that page") shown through the app's one toast
// system (LUM-993). The message
// is shown once per navigation (guarded by location.key, so React StrictMode's
// double effect cannot show it twice) and cleared from history state at once,
// so a reload or Back does not replay it. Anything that is not a non-empty
// string is ignored.

import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";

export function RouteFlash(): null {
  const loc = useLocation();
  const nav = useNavigate();
  const shownFor = useRef<string | null>(null);
  const raw = (loc.state as { toast?: unknown } | null)?.toast;
  const message = typeof raw === "string" && raw.trim().length > 0 ? raw : null;

  useEffect(() => {
    if (message === null || shownFor.current === loc.key) return;
    shownFor.current = loc.key;
    toast(message);
    const rest = { ...((loc.state ?? {}) as Record<string, unknown>) };
    delete rest.toast;
    nav(
      { pathname: loc.pathname, search: loc.search, hash: loc.hash },
      { replace: true, state: Object.keys(rest).length ? rest : null },
    );
  }, [message, loc.key, loc.pathname, loc.search, loc.hash, loc.state, nav]);

  return null;
}
