// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Entry shell. Wires AuthProvider → RequireAuth → AppShell → react-router-dom.
// Pass 1.1 shipped auth + shell + design-token foundation.
// Pass 1.2 added ChatPage (streaming chat).
// Pass 1.3 added SearchPage; Pass 1.4 ApprovalsPage; Pass 1.5 URL routes + Caddy.
// Admin + Me shells (`lumogis_web_admin_shell` plan): `/me/*`, `/admin/*`.

import { useCallback, useEffect, useMemo } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";

import { AuthAdminRouteRefetch } from "./auth/AuthAdminRouteRefetch";
import { AuthProvider, RequireAuth, useUser } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import { NAV_ITEMS, type NavItem } from "./components/BottomNav";
import { ChatPage } from "./features/chat/ChatPage";
import { SearchPage } from "./features/memory/SearchPage";
import { ApprovalsPage } from "./features/approvals/ApprovalsPage";
import { MePage } from "./features/me/MePage";
import { MeProfileView } from "./features/me/MeProfileView";
import { MeConnectorsView } from "./features/me/MeConnectorsView";
import { MePermissionsView } from "./features/me/MePermissionsView";
import { MeLlmProvidersView } from "./features/me/MeLlmProvidersView";
import { MeMcpTokensView } from "./features/me/MeMcpTokensView";
import { MeNotificationsView } from "./features/me/MeNotificationsView";
import { MeExportView } from "./features/me/MeExportView";
import { MeToolsCapabilitiesView } from "./features/me/MeToolsCapabilitiesView";
import { AdminPage } from "./features/admin/AdminPage";
import { AdminUsersView } from "./features/admin/AdminUsersView";
import { AdminConnectorCredentialsView } from "./features/admin/AdminConnectorCredentialsView";
import { AdminConnectorPermissionsView } from "./features/admin/AdminConnectorPermissionsView";
import { AdminMcpTokensView } from "./features/admin/AdminMcpTokensView";
import { AdminAuditView } from "./features/admin/AdminAuditView";
import { AdminDiagnosticsView } from "./features/admin/AdminDiagnosticsView";
import { QuickCapturePage } from "./features/capture/QuickCapturePage";

function pathToNavKey(pathname: string): string {
  if (pathname.startsWith("/search")) return "search";
  if (pathname.startsWith("/capture")) return "capture";
  if (pathname.startsWith("/approvals")) return "approvals";
  if (pathname.startsWith("/me")) return "me";
  if (pathname.startsWith("/admin")) return "admin";
  return "chat";
}

export function App(): JSX.Element {
  return (
    <BrowserRouter>
      <AuthProvider>
        <RequireAuth>
          <AuthAdminRouteRefetch />
          <ShellRoutes />
        </RequireAuth>
      </AuthProvider>
    </BrowserRouter>
  );
}

const EXTRA_NAV: ReadonlyArray<NavItem> = [
  { key: "me", label: "Settings", href: "/me" },
  { key: "admin", label: "Admin", href: "/admin" },
];

function ShellRoutes(): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useUser();
  const activeKey = useMemo(() => pathToNavKey(location.pathname), [location.pathname]);

  const navItems = useMemo((): ReadonlyArray<NavItem> => {
    const base = [...NAV_ITEMS];
    const extra = [EXTRA_NAV[0]!];
    if (user?.role === "admin") {
      extra.push(EXTRA_NAV[1]!);
    }
    return [...base, ...extra];
  }, [user?.role]);

  const handleNavigate = useCallback(
    (key: string) => {
      if (key === "chat" || key === "search" || key === "capture" || key === "approvals" || key === "me" || key === "admin") {
        if (key === "chat") navigate("/chat");
        else if (key === "search") navigate("/search");
        else if (key === "capture") navigate("/capture");
        else if (key === "approvals") navigate("/approvals");
        else if (key === "me") navigate("/me");
        else if (key === "admin") navigate("/admin");
      }
    },
    [navigate],
  );

  return (
    <>
      <RouteFlash />
      <AppShell activeKey={activeKey} onNavigate={handleNavigate} navItems={navItems}>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/capture" element={<QuickCapturePage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/me" element={<MePage />}>
            <Route index element={<Navigate to="profile" replace />} />
            <Route path="profile" element={<MeProfileView />} />
            <Route path="connectors" element={<MeConnectorsView />} />
            <Route path="permissions" element={<MePermissionsView />} />
            <Route path="tools-capabilities" element={<MeToolsCapabilitiesView />} />
            <Route path="llm-providers" element={<MeLlmProvidersView />} />
            <Route path="mcp-tokens" element={<MeMcpTokensView />} />
            <Route path="notifications" element={<MeNotificationsView />} />
            <Route path="export" element={<MeExportView />} />
            <Route path="*" element={<Navigate to="profile" replace />} />
          </Route>
          <Route path="/admin" element={<AdminPage />}>
            <Route index element={<Navigate to="users" replace />} />
            <Route path="users" element={<AdminUsersView />} />
            <Route path="connector-credentials" element={<AdminConnectorCredentialsView />} />
            <Route path="connector-permissions" element={<AdminConnectorPermissionsView />} />
            <Route path="mcp-tokens" element={<AdminMcpTokensView />} />
            <Route path="audit" element={<AdminAuditView />} />
            <Route path="diagnostics" element={<AdminDiagnosticsView />} />
            <Route path="*" element={<Navigate to="users" replace />} />
          </Route>
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </AppShell>
    </>
  );
}

function RouteFlash(): JSX.Element | null {
  const loc = useLocation();
  const nav = useNavigate();
  const toast = (loc.state as { toast?: string } | null)?.toast;

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => {
      const s = (loc.state ?? {}) as { toast?: string } & Record<string, unknown>;
      const rest = { ...s };
      delete rest.toast;
      nav(
        { pathname: loc.pathname, search: loc.search, hash: loc.hash },
        { replace: true, state: Object.keys(rest).length ? rest : null },
      );
    }, 6000);
    return () => window.clearTimeout(t);
  }, [toast, loc.pathname, loc.search, loc.hash, loc.state, nav]);

  if (!toast) return null;
  return (
    <p
      role="status"
      style={{
        margin: 0,
        padding: "0.5rem 1rem",
        background: "rgba(255, 180, 50, 0.12)",
        borderBottom: "1px solid rgba(255, 200, 100, 0.25)",
        textAlign: "center",
      }}
    >
      {toast}
    </p>
  );
}
