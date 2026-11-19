// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The places in Lumogis Web's navigation (LUM-993 Pass 2a). The sidebar and the
// phone bar render from these lists; App.tsx decides which optional items show
// (Email once a mailbox is connected, Approvals once something can act).

import {
  CheckCheck,
  Library,
  Mail,
  MessageSquare,
  NotebookPen,
  Search,
  Settings,
  type LucideIcon,
} from "lucide-react";

export type NavKey = "chat" | "search" | "documents" | "capture" | "email" | "approvals" | "settings";

export interface NavItem {
  key: NavKey;
  label: string;
  href: string;
  icon: LucideIcon;
}

export const CHAT: NavItem = { key: "chat", label: "Chat", href: "/chat", icon: MessageSquare };
export const SEARCH: NavItem = { key: "search", label: "Search", href: "/search", icon: Search };
export const LIBRARY: NavItem = { key: "documents", label: "Library", href: "/documents", icon: Library };
export const CAPTURE: NavItem = { key: "capture", label: "Capture", href: "/capture", icon: NotebookPen };
export const EMAIL: NavItem = { key: "email", label: "Email", href: "/email", icon: Mail };
export const APPROVALS: NavItem = { key: "approvals", label: "Approvals", href: "/approvals", icon: CheckCheck };
// One Settings since LUM-993 Pass 3; household pages live inside it, so there is no Admin item.
export const SETTINGS: NavItem = { key: "settings", label: "Settings", href: "/settings", icon: Settings };

/** Always there, in this order; the phone bar shows these plus a fifth tab (More, or Settings). */
export const CORE_ITEMS: ReadonlyArray<NavItem> = [CHAT, SEARCH, LIBRARY, CAPTURE];

/** Which navigation item a path belongs to. */
export function pathToNavKey(pathname: string): NavKey {
  if (pathname.startsWith("/documents")) return "documents";
  if (pathname.startsWith("/email")) return "email";
  if (pathname.startsWith("/search")) return "search";
  // LUM-161: "What Lumogis knows" is reached from Search; no top-level nav item.
  if (pathname.startsWith("/entities")) return "search";
  if (pathname.startsWith("/capture")) return "capture";
  if (pathname.startsWith("/approvals")) return "approvals";
  if (["/settings", "/me", "/admin", "/audit"].some((p) => pathname === p || pathname.startsWith(`${p}/`))) return "settings";
  return "chat";
}

export interface ShellNavInput {
  /** A mailbox is connected (LUM-642/655/658 activation signal). */
  emailActive: boolean;
  /** Something that can act is connected, or an item is waiting (Decision 18). */
  canAct: boolean;
  isAdmin: boolean;
  pendingApprovals?: number;
}

export interface ShellNavLists {
  primary: ReadonlyArray<NavItem>;
  secondary: ReadonlyArray<NavItem>;
  tabs: ReadonlyArray<NavItem>;
  more: ReadonlyArray<NavItem>;
  pendingApprovals?: number;
}

/**
 * What the menu shows (LUM-993 Decision 16): the four core places always; Email
 * once a mailbox is connected; Approvals once something can act; Settings
 * (which holds the household pages for admins). The phone bar keeps the core four and puts the
 * rest in More; when the rest is only Settings, Settings is the fifth tab instead.
 */
export function buildShellNav({ emailActive, canAct, isAdmin, pendingApprovals }: ShellNavInput): ShellNavLists {
  const optional: NavItem[] = [];
  if (emailActive) optional.push(EMAIL);
  if (canAct) optional.push(APPROVALS);
  // isAdmin is kept in the input for future items; household pages live in Settings.
  void isAdmin;
  const secondary: NavItem[] = [SETTINGS];
  // A More sheet holding only Settings is an extra tap for nothing: Settings is
  // then the fifth tab itself, and Sign out sits on the Settings start page.
  const onlySettings = optional.length === 0;
  return {
    primary: [...CORE_ITEMS, ...optional],
    secondary,
    tabs: onlySettings ? [...CORE_ITEMS, SETTINGS] : CORE_ITEMS,
    more: onlySettings ? [] : [...optional, ...secondary],
    pendingApprovals: canAct ? pendingApprovals : undefined,
  };
}
