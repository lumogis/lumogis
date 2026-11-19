// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The desktop sidebar (LUM-993 Pass 2a). Top: the Lumogis wordmark and the
// collapse button. Middle: the places (Chat, Search, Library, Capture, then
// Email and Approvals when they apply). Bottom: Settings and the account menu. Collapsed, it shows icons
// with tooltips; the choice is kept per browser (sidebarCollapse.ts).

import { ChevronsUpDown, LogOut, Palette, PanelLeft, User } from "lucide-react";
import { useEffect, useState, type MouseEvent } from "react";

import { useAuth, useUser } from "../../auth/AuthProvider";
import { getSidebarCollapsed, setSidebarCollapsed } from "../../design/sidebarCollapse";
import { Avatar, AvatarFallback } from "../ui/avatar";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";

import { initialsFor } from "./initials";
import type { NavItem, NavKey } from "./navItems";
import { cn } from "@/lib/utils";

export interface AppSidebarProps {
  primary: ReadonlyArray<NavItem>;
  secondary: ReadonlyArray<NavItem>;
  activeKey: NavKey;
  onNavigate: (href: string) => void;
  /** Count shown on Approvals; 0 or undefined shows none. */
  pendingApprovals?: number;
  className?: string;
}

function NavRow({
  item,
  active,
  collapsed,
  badge,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  badge?: number;
  onNavigate: (href: string) => void;
}): JSX.Element {
  const Icon = item.icon;
  const showBadge = badge !== undefined && badge > 0;
  const label = showBadge ? `${item.label}, ${badge} waiting` : item.label;
  const link = (
    <a
      href={item.href}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed || showBadge ? label : undefined}
      onClick={(e: MouseEvent<HTMLAnchorElement>) => {
        // Let the browser handle new-tab clicks; everything else stays in the app.
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        onNavigate(item.href);
      }}
      className={cn(
        "flex h-9 items-center gap-3 rounded-md px-2.5 text-sm font-medium no-underline transition-colors pointer-coarse:min-h-[44px]",
        "outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        active
          ? "bg-accent-soft text-accent-text hover:text-accent-text"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
        collapsed && "justify-center px-0",
      )}
    >
      <Icon className="size-[18px] shrink-0" aria-hidden="true" />
      {collapsed ? null : <span className="min-w-0 flex-1 truncate">{item.label}</span>}
      {showBadge && !collapsed ? (
        <Badge variant="secondary" className="ml-auto tabular-nums" aria-hidden="true">
          {badge}
        </Badge>
      ) : null}
      {showBadge && collapsed ? (
        <span className="absolute top-1 right-1 size-2 rounded-full bg-primary" aria-hidden="true" />
      ) : null}
    </a>
  );
  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="relative block" />}>{link}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

function AccountMenu({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate: (href: string) => void;
}): JSX.Element | null {
  const user = useUser();
  const { logout } = useAuth();
  if (!user) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Account menu"
        title={user.email}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md p-1.5 text-left text-sm outline-none transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 pointer-coarse:min-h-[44px]",
          collapsed && "justify-center",
        )}
      >
        <Avatar size="sm">
          <AvatarFallback>{initialsFor(user.email)}</AvatarFallback>
        </Avatar>
        {collapsed ? null : (
          <>
            <span className="min-w-0 flex-1 truncate">{user.email}</span>
            <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side={collapsed ? "right" : "top"} align="start" className="min-w-56">
        <div className="px-2 py-1.5 text-xs text-muted-foreground">
          <div className="truncate text-sm text-foreground">{user.email}</div>
          {user.role === "admin" ? "Household admin" : "Member"}
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => onNavigate("/settings/profile")}>
          <User aria-hidden="true" />
          Profile
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onNavigate("/settings/appearance")}>
          <Palette aria-hidden="true" />
          Look and feel
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={() => void logout()}>
          <LogOut aria-hidden="true" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppSidebar({
  primary,
  secondary,
  activeKey,
  onNavigate,
  pendingApprovals,
  className,
}: AppSidebarProps): JSX.Element {
  const [collapsed, setCollapsed] = useState(() => getSidebarCollapsed());

  useEffect(() => {
    setSidebarCollapsed(collapsed);
  }, [collapsed]);

  const row = (item: NavItem) => (
    <NavRow
      key={item.key}
      item={item}
      active={item.key === activeKey}
      collapsed={collapsed}
      badge={item.key === "approvals" ? pendingApprovals : undefined}
      onNavigate={onNavigate}
    />
  );

  return (
    <nav
      aria-label="Primary navigation"
      data-collapsed={collapsed ? "" : undefined}
      // Collapsed, the column is 64 px: p-2 leaves 48 px, room for 44 px touch targets.
      className={cn("flex min-h-0 flex-col gap-1", collapsed ? "p-2" : "p-3", className)}
    >
      <div className={cn("mb-3 flex h-9 items-center", collapsed ? "justify-center" : "justify-between pl-2.5")}>
        {collapsed ? null : (
          <span className="font-heading text-lg leading-none font-bold tracking-tight">Lumogis</span>
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={() => setCollapsed((v) => !v)}
        >
          <PanelLeft aria-hidden="true" />
        </Button>
      </div>
      <div className="flex flex-col gap-0.5">{primary.map(row)}</div>
      <div className="flex-1" aria-hidden="true" />
      <div className="flex flex-col gap-0.5 border-t border-border pt-2">{secondary.map(row)}</div>
      <div className="pt-1">
        <AccountMenu collapsed={collapsed} onNavigate={onNavigate} />
      </div>
    </nav>
  );
}
