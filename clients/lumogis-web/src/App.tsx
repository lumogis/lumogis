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
import { useQuery } from "@tanstack/react-query";

import { AuthAdminRouteRefetch } from "./auth/AuthAdminRouteRefetch";
import { AuthProvider, RequireAuth, useAuth, useUser } from "./auth/AuthProvider";
import { listSources } from "./api/emailSources";
import { EmailAttachmentsPage } from "./features/email/EmailAttachmentsPage";
import { EmailInboxView } from "./features/email/EmailInboxView";
import { isEmailActivated, readCachedEmailActivated, writeCachedEmailActivated } from "./features/email/activation";
import { AppShell, type ShellNav } from "./components/AppShell";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { buildShellNav, pathToNavKey } from "./components/shell/navItems";
import { useShellState } from "./api/meShell";
import { ChatPage } from "./features/chat/ChatPage";
import { SearchPage } from "./features/memory/SearchPage";
import { ApprovalsPage } from "./features/approvals/ApprovalsPage";
import { QuickCapturePage } from "./features/capture/QuickCapturePage";
import { DocumentChatPage } from "./features/document-chat/DocumentChatPage";
import { DocumentsPage } from "./features/documents/DocumentsPage";
import { DocumentDetailView } from "./features/documents/DocumentDetailView";
import { EntityDetailPage } from "./features/entities/EntityDetailPage";
import { EntityListPage } from "./features/entities/EntityListPage";
import { InviteRedemptionPage } from "./features/invite/InviteRedemptionPage";
import { ForgotPasswordPage } from "./features/auth/ForgotPasswordPage";
import { ResetPasswordPage } from "./features/auth/ResetPasswordPage";
import { SetupPage } from "./features/setup/SetupPage";
import { OnboardingGate } from "./features/onboarding/OnboardingGate";
import { RouteFlash } from "./components/RouteFlash";
import { LegacyRedirect } from "./features/settings/LegacyRedirect";
import { SettingsIndex } from "./features/settings/SettingsHome";
import { SettingsLayout } from "./features/settings/SettingsLayout";
import { SettingsPage } from "./features/settings/SettingsPage";
import { legacyRedirectMap } from "./features/settings/settingsNav";

const LEGACY_REDIRECTS = legacyRedirectMap();
import { Toaster } from "./components/ui/sonner";

export function App(): JSX.Element {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/invite" element={<InviteRedemptionPage />} />
          {/* LUM-65: siblings of /invite, deliberately OUTSIDE <RequireAuth>.
              Anything under `/*` renders <LoginForm/> for an anonymous
              visitor — and the only visitor these two pages exist for is the
              one who cannot sign in. */}
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          {/* LUM-981: the first-admin setup page, also outside <RequireAuth>. */}
          <Route path="/setup" element={<SetupPage />} />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <AuthAdminRouteRefetch />
                <ShellRoutes />
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

function ShellRoutes(): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useUser();
  const { client } = useAuth();
  const activeKey = useMemo(() => pathToNavKey(location.pathname), [location.pathname]);

  // LUM-642/LUM-655: email is a core opt-in integration — the Email destination
  // appears only once a mailbox is configured. Fail closed (hidden) while the
  // sources query is loading or errors; it pops in ~1 RTT after shell render on
  // a cold load (documented; a persisted-activation follow-up removes the CLS).
  const emailSourcesQ = useQuery({
    queryKey: ["email", "sources"],
    queryFn: () => listSources(client),
  });
  // LUM-658: seed from last-known activation so the Email item renders on the
  // first paint of a cold load; reconcile with the fresh fetch when it resolves.
  const freshEmailActive = emailSourcesQ.data
    ? isEmailActivated(emailSourcesQ.data.sources)
    : undefined;
  const emailActive = freshEmailActive ?? readCachedEmailActivated();
  useEffect(() => {
    if (freshEmailActive !== undefined) writeCachedEmailActivated(freshEmailActive);
  }, [freshEmailActive]);

  // LUM-993 Decision 16: Approvals appears only once something that can act is
  // connected (or an item is waiting); hidden until /api/v1/me/shell answers.
  const shell = useShellState();
  const canAct = shell?.can_act === true;
  const pendingApprovals = shell?.pending_approvals;

  const nav = useMemo(
    (): ShellNav =>
      buildShellNav({ emailActive, canAct, isAdmin: user?.role === "admin", pendingApprovals }),
    [user?.role, emailActive, canAct, pendingApprovals],
  );

  const handleNavigate = useCallback((href: string) => navigate(href), [navigate]);

  return (
    // Outer boundary: last-resort net for crashes in the shell/nav itself.
    // Inner boundary (around <Routes>) keeps the nav usable when a page crashes.
    <AppErrorBoundary>
      <OnboardingGate />
      <RouteFlash />
      <Toaster position="top-center" />
      <AppShell activeKey={activeKey} onNavigate={handleNavigate} nav={nav}>
        <AppErrorBoundary>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/documents/:documentId/chat" element={<DocumentChatPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/email" element={<EmailInboxView />} />
          {/* LUM-742: nested under /email so no sixth top-level nav item is
              needed — `pathToNavKey` already matches on startsWith("/email"),
              so the Email item stays highlighted here. */}
          <Route path="/email/attachments" element={<EmailAttachmentsPage />} />
          <Route path="/capture" element={<QuickCapturePage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/documents/:documentId" element={<DocumentDetailView />} />
          {/* LUM-161: nested under Search (LUM-742 rule) so no new top-level nav
              item is needed — `pathToNavKey` maps /entities* to "search". */}
          <Route path="/entities" element={<EntityListPage />} />
          <Route path="/entities/:entityId" element={<EntityDetailPage />} />
          {/* LUM-993 Pass 3: one Settings. Every page comes from settingsNav. */}
          <Route path="/settings" element={<SettingsLayout />}>
            <Route index element={<SettingsIndex />} />
            <Route path=":slug" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/settings/profile" replace />} />
          </Route>
          {/* Old addresses (/me/..., /admin/..., /audit) open their new page. */}
          {Object.entries(LEGACY_REDIRECTS).map(([from, to]) => (
            <Route key={from} path={from} element={<LegacyRedirect to={to} />} />
          ))}
          <Route path="/me/*" element={<LegacyRedirect to="/settings/profile" />} />
          <Route path="/admin/*" element={<LegacyRedirect to="/settings/members" />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
        </AppErrorBoundary>
      </AppShell>
    </AppErrorBoundary>
  );
}
