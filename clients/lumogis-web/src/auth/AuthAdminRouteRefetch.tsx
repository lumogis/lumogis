// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// `lumogis_web_admin_shell` plan — refetch /auth/me on /admin/* navigation when
// data is older than 5s (stale-role hazard mitigation).
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const ME_KEY = ["auth", "me"] as const;

export function AuthAdminRouteRefetch(): null {
  const loc = useLocation();
  const qc = useQueryClient();

  useEffect(() => {
    if (!loc.pathname.startsWith("/admin")) return;
    const st = qc.getQueryState(ME_KEY);
    const du = st?.dataUpdatedAt ?? 0;
    if (Date.now() - du <= 5000) return;
    void qc.invalidateQueries({ queryKey: ME_KEY, refetchType: "active" });
  }, [loc.pathname, qc]);

  return null;
}
