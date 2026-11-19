// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// One Settings (LUM-993 Pass 3): the single list of every Settings page — its
// group, title, icon, help text, who may see it, and the old addresses it
// replaces. The menu, the index, the routes and the old-address redirects are
// all built from this list (tests/features/settings/settingsNav.test.ts).

import {
  Bell,
  Download,
  EyeOff,
  Flag,
  FlaskConical,
  FolderOpen,
  Globe,
  HeartPulse,
  History,
  KeyRound,
  Lock,
  Mail,
  Network,
  Palette,
  Plug,
  Send,
  Share2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Star,
  Stethoscope,
  Tags,
  User,
  Users,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { ComponentType } from "react";

import type { help } from "../../copy/help";
import { AdminAuditView } from "../admin/AdminAuditView";
import { AdminConnectorCredentialsView } from "../admin/AdminConnectorCredentialsView";
import { AdminConnectorPermissionsView } from "../admin/AdminConnectorPermissionsView";
import { AdminDiagnosticsView } from "../admin/AdminDiagnosticsView";
import { AdminFeatureFlagsView } from "../admin/AdminFeatureFlagsView";
import { AdminKnowledgeGraphView } from "../admin/AdminKnowledgeGraphView";
import { AdminMcpTokensView } from "../admin/AdminMcpTokensView";
import { AdminPrivacyModeView } from "../admin/AdminPrivacyModeView";
import { AdminSafetyPlaygroundView } from "../admin/AdminSafetyPlaygroundView";
import { AdminSearchSettingsView } from "../admin/AdminSearchSettingsView";
import { AdminSecureAddressView } from "../admin/AdminSecureAddressView";
import { AdminSharedItemsView } from "../admin/AdminSharedItemsView";
import { AdminSystemMailView } from "../admin/AdminSystemMailView";
import { AdminSystemStatusView } from "../admin/AdminSystemStatusView";
import { AdminUsersView } from "../admin/AdminUsersView";
import { AuditLogView } from "../audit/AuditLogView";
import { MeAppearanceView } from "../me/MeAppearanceView";
import { MeConnectorsView } from "../me/MeConnectorsView";
import { MeEmailLabelsView } from "../me/MeEmailLabelsView";
import { MeExportView } from "../me/MeExportView";
import { MeLlmProvidersView } from "../me/MeLlmProvidersView";
import { MeMcpTokensView } from "../me/MeMcpTokensView";
import { MeNotificationsView } from "../me/MeNotificationsView";
import { MePermissionsView } from "../me/MePermissionsView";
import { MePrivacyModeView } from "../me/MePrivacyModeView";
import { MeProfileView } from "../me/MeProfileView";
import { MeRelevanceView } from "../me/MeRelevanceView";
import { MeSharedItemsView } from "../me/MeSharedItemsView";
import { MeToolsCapabilitiesView } from "../me/MeToolsCapabilitiesView";
import { MeWontLearnView } from "../me/MeWontLearnView";
import { SourcesView } from "../me/SourcesView";
import { EmailSignalsView } from "../signals/EmailSignalsView";

export type GroupId = "you" | "email" | "connections" | "privacy" | "household" | "advanced" | "developers";
export type Audience = "everyone" | "admin" | "developer";
export type HelpPage = keyof typeof help;

export interface SettingsGroup {
  id: GroupId;
  title: string;
  /** Advanced starts closed in the menu. */
  collapsible?: boolean;
}

export const GROUPS: ReadonlyArray<SettingsGroup> = [
  { id: "you", title: "You" },
  { id: "email", title: "Email and documents" },
  { id: "connections", title: "Connections" },
  { id: "privacy", title: "Privacy and control" },
  { id: "household", title: "Household" },
  { id: "advanced", title: "Advanced", collapsible: true },
  { id: "developers", title: "For developers" },
];

interface EntryBase {
  group: GroupId;
  slug: string;
  title: string;
  icon: LucideIcon;
  /** Written per entry (not derived from the slug): the page's purpose line and (i). */
  helpKey: HelpPage;
  audience: Audience;
  /** Old addresses that now open this page. */
  redirectsFrom: ReadonlyArray<string>;
}

/** A page that is the same for everyone. */
export interface PlainEntry extends EntryBase {
  Component: ComponentType;
  scope?: never;
}

/** A page with a Mine view and, for admins, a Household view (`?view=household`). */
export interface ScopedEntry extends EntryBase {
  Component?: never;
  scope: { mine: ComponentType; household: ComponentType };
}

export type SettingsEntry = PlainEntry | ScopedEntry;

// Views written before LUM-993 take no props; ComponentType<object> keeps them assignable.
const C = (c: ComponentType<never>): ComponentType => c as ComponentType;

export const SETTINGS: ReadonlyArray<SettingsEntry> = [
  // You
  { group: "you", slug: "profile", title: "Profile", icon: User, helpKey: "profile", audience: "everyone", Component: C(MeProfileView), redirectsFrom: ["/me/profile"] },
  { group: "you", slug: "appearance", title: "Look and feel", icon: Palette, helpKey: "appearance", audience: "everyone", Component: C(MeAppearanceView), redirectsFrom: ["/me/appearance"] },
  { group: "you", slug: "notifications", title: "Notifications", icon: Bell, helpKey: "notifications", audience: "everyone", Component: C(MeNotificationsView), redirectsFrom: ["/me/notifications"] },
  // Email and documents
  { group: "email", slug: "email", title: "Email accounts", icon: Mail, helpKey: "email", audience: "everyone", Component: C(EmailSignalsView), redirectsFrom: ["/me/email-signals"] },
  { group: "email", slug: "email-labels", title: "Email labels and rules", icon: Tags, helpKey: "emailLabels", audience: "everyone", Component: C(MeEmailLabelsView), redirectsFrom: ["/me/email-labels"] },
  { group: "email", slug: "what-matters", title: "What matters to you", icon: Star, helpKey: "whatMatters", audience: "everyone", Component: C(MeRelevanceView), redirectsFrom: ["/me/relevance"] },
  { group: "email", slug: "sources", title: "Documents, feeds and pages", icon: FolderOpen, helpKey: "sources", audience: "everyone", Component: C(SourcesView), redirectsFrom: ["/me/sources"] },
  // Connections
  { group: "connections", slug: "connections", title: "Connected services", icon: Plug, helpKey: "connections", audience: "everyone", scope: { mine: C(MeConnectorsView), household: C(AdminConnectorCredentialsView) }, redirectsFrom: ["/me/connectors", "/admin/connector-credentials"] },
  { group: "connections", slug: "ai-services", title: "AI services", icon: Sparkles, helpKey: "aiServices", audience: "everyone", Component: C(MeLlmProvidersView), redirectsFrom: ["/me/llm-providers"] },
  { group: "connections", slug: "connected-apps", title: "Connected AI apps", icon: KeyRound, helpKey: "connectedApps", audience: "everyone", scope: { mine: C(MeMcpTokensView), household: C(AdminMcpTokensView) }, redirectsFrom: ["/me/mcp-tokens", "/admin/mcp-tokens"] },
  // Privacy and control
  { group: "privacy", slug: "permissions", title: "What Lumogis may do", icon: ShieldCheck, helpKey: "permissions", audience: "everyone", scope: { mine: C(MePermissionsView), household: C(AdminConnectorPermissionsView) }, redirectsFrom: ["/me/permissions", "/admin/connector-permissions"] },
  { group: "privacy", slug: "privacy", title: "Online or local AI", icon: Lock, helpKey: "privacy", audience: "everyone", scope: { mine: C(MePrivacyModeView), household: C(AdminPrivacyModeView) }, redirectsFrom: ["/me/privacy-mode", "/admin/privacy-mode"] },
  { group: "privacy", slug: "shared", title: "Shared with the household", icon: Share2, helpKey: "shared", audience: "everyone", scope: { mine: C(MeSharedItemsView), household: C(AdminSharedItemsView) }, redirectsFrom: ["/me/shared-items", "/admin/shared-items"] },
  { group: "privacy", slug: "wont-learn", title: "Names Lumogis won't learn", icon: EyeOff, helpKey: "wontLearn", audience: "everyone", Component: C(MeWontLearnView), redirectsFrom: ["/me/wont-learn"] },
  { group: "privacy", slug: "activity", title: "Activity", icon: History, helpKey: "activity", audience: "everyone", scope: { mine: C(AuditLogView), household: C(AdminAuditView) }, redirectsFrom: ["/audit", "/admin/audit"] },
  { group: "privacy", slug: "export", title: "Download my data", icon: Download, helpKey: "export", audience: "everyone", Component: C(MeExportView), redirectsFrom: ["/me/export"] },
  // Household (admin)
  { group: "household", slug: "members", title: "Members and invites", icon: Users, helpKey: "members", audience: "admin", Component: C(AdminUsersView), redirectsFrom: ["/admin/users"] },
  { group: "household", slug: "health", title: "Health and backups", icon: HeartPulse, helpKey: "health", audience: "admin", Component: C(AdminSystemStatusView), redirectsFrom: ["/admin/system-status"] },
  { group: "household", slug: "home-address", title: "Home address", icon: Globe, helpKey: "homeAddress", audience: "admin", Component: C(AdminSecureAddressView), redirectsFrom: ["/admin/secure-address"] },
  { group: "household", slug: "system-mail", title: "Email Lumogis sends from", icon: Send, helpKey: "systemMail", audience: "admin", Component: C(AdminSystemMailView), redirectsFrom: ["/admin/system-mail"] },
  // Advanced (admin, collapsed)
  { group: "advanced", slug: "search-ranking", title: "Better search ranking", icon: SlidersHorizontal, helpKey: "searchRanking", audience: "admin", Component: C(AdminSearchSettingsView), redirectsFrom: ["/admin/search-settings"] },
  { group: "advanced", slug: "knowledge-graph", title: "Knowledge graph", icon: Network, helpKey: "knowledgeGraph", audience: "admin", Component: C(AdminKnowledgeGraphView), redirectsFrom: ["/admin/knowledge-graph"] },
  // For developers (admin + switch)
  { group: "developers", slug: "diagnostics", title: "Diagnostics", icon: Stethoscope, helpKey: "diagnostics", audience: "developer", Component: C(AdminDiagnosticsView), redirectsFrom: ["/admin/diagnostics"] },
  { group: "developers", slug: "tools", title: "Tools and capabilities", icon: Wrench, helpKey: "tools", audience: "developer", Component: C(MeToolsCapabilitiesView), redirectsFrom: ["/me/tools-capabilities"] },
  { group: "developers", slug: "feature-flags", title: "Feature flags", icon: Flag, helpKey: "featureFlags", audience: "developer", Component: C(AdminFeatureFlagsView), redirectsFrom: ["/admin/feature-flags"] },
  { group: "developers", slug: "safety", title: "Safety test bench", icon: FlaskConical, helpKey: "safety", audience: "developer", Component: C(AdminSafetyPlaygroundView), redirectsFrom: ["/admin/safety"] },
];

export function entryBySlug(slug: string | undefined): SettingsEntry | undefined {
  return SETTINGS.find((e) => e.slug === slug);
}

export interface Viewer {
  isAdmin: boolean;
  /** "Show developer tools" is on in this browser. */
  devTools: boolean;
}

/** Whether a page appears in the menu and the index for this viewer. */
export function isListed(entry: SettingsEntry, { isAdmin, devTools }: Viewer): boolean {
  if (entry.audience === "everyone") return true;
  if (entry.audience === "admin") return isAdmin;
  return isAdmin && devTools;
}

/** Whether the viewer may open the page at all (developer pages open from a link with the switch off). */
export function mayOpen(entry: SettingsEntry, { isAdmin }: Pick<Viewer, "isAdmin">): boolean {
  return entry.audience === "everyone" || isAdmin;
}

/** Groups in order with their listed pages, empty groups left out. */
export function listedGroups(viewer: Viewer): Array<{ group: SettingsGroup; entries: SettingsEntry[] }> {
  return GROUPS.map((group) => ({
    group,
    entries: SETTINGS.filter((e) => e.group === group.id && isListed(e, viewer)),
  })).filter((g) => g.entries.length > 0);
}

/**
 * Old address → new address. An old household address (`/admin/...`) of a page
 * with Mine and Household views opens the Household view; `/me` opens Profile
 * and `/admin` opens Members.
 */
export function legacyRedirectMap(): Record<string, string> {
  const map: Record<string, string> = { "/me": "/settings/profile", "/admin": "/settings/members" };
  for (const e of SETTINGS) {
    for (const old of e.redirectsFrom) {
      const household = e.scope !== undefined && old.startsWith("/admin/");
      map[old] = `/settings/${e.slug}${household ? "?view=household" : ""}`;
    }
  }
  return map;
}
