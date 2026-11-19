// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The phone tab bar (LUM-993 Pass 2a): Chat, Search, Library, Capture and More.
// More opens a sheet from the bottom with everything else (Email and Approvals
// when they apply, Settings, Sign out). The More tab is the
// current tab whenever the page lives in that sheet.
// With nothing but Settings to put there, Settings is the fifth tab and there
// is no More (LUM-993 follow-up).

import { LogOut, Menu } from "lucide-react";
import { useState, type MouseEvent } from "react";

import { useAuth, useUser } from "../../auth/AuthProvider";
import { Badge } from "../ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "../ui/sheet";

import type { NavItem, NavKey } from "./navItems";
import { cn } from "@/lib/utils";

export interface MobileNavProps {
  tabs: ReadonlyArray<NavItem>;
  more: ReadonlyArray<NavItem>;
  activeKey: NavKey;
  onNavigate: (href: string) => void;
  pendingApprovals?: number;
  className?: string;
}

const tabClass = (active: boolean) =>
  cn(
    "flex min-h-14 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-md text-[11px] font-medium no-underline outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
    active ? "text-accent-text hover:text-accent-text" : "text-muted-foreground hover:text-foreground",
  );

export function MobileNav({
  tabs,
  more,
  activeKey,
  onNavigate,
  pendingApprovals,
  className,
}: MobileNavProps): JSX.Element {
  const user = useUser();
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const moreActive = more.some((m) => m.key === activeKey);
  const waiting = pendingApprovals !== undefined && pendingApprovals > 0 && more.some((m) => m.key === "approvals");

  const go = (href: string) => (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    setOpen(false);
    onNavigate(href);
  };

  return (
    <nav aria-label="Primary navigation" className={cn("flex items-stretch gap-1 px-1", className)}>
      {tabs.map((item) => {
        const Icon = item.icon;
        const active = item.key === activeKey;
        return (
          <a key={item.key} href={item.href} aria-current={active ? "page" : undefined} onClick={go(item.href)} className={tabClass(active)}>
            <Icon className="size-5" aria-hidden="true" />
            <span className="max-w-full truncate">{item.label}</span>
          </a>
        );
      })}
      {more.length > 0 ? (
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger
            aria-current={moreActive ? "page" : undefined}
            aria-label={waiting ? `More, ${pendingApprovals} waiting` : "More"}
            className={cn(tabClass(moreActive), "relative")}
          >
            <Menu className="size-5" aria-hidden="true" />
            <span>More</span>
            {waiting ? <span className="absolute top-2 right-[calc(50%-14px)] size-2 rounded-full bg-primary" aria-hidden="true" /> : null}
          </SheetTrigger>
          <SheetContent side="bottom" className="gap-0 rounded-t-xl pb-[max(1rem,env(safe-area-inset-bottom))]">
            <SheetHeader className="pb-2">
              <SheetTitle>More</SheetTitle>
              <SheetDescription className="sr-only">Everything else in Lumogis.</SheetDescription>
            </SheetHeader>
            <ul className="m-0 flex list-none flex-col gap-1 px-4 pb-2">
              {more.map((item) => {
                const Icon = item.icon;
                const active = item.key === activeKey;
                const badge = item.key === "approvals" && waiting ? pendingApprovals : undefined;
                return (
                  <li key={item.key}>
                    <a
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      onClick={go(item.href)}
                      className={cn(
                        "flex min-h-12 items-center gap-3 rounded-md px-3 text-sm font-medium no-underline outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                        active ? "bg-accent-soft text-accent-text hover:text-accent-text" : "text-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      <Icon className="size-5" aria-hidden="true" />
                      <span className="flex-1">{item.label}</span>
                      {badge !== undefined ? (
                        <Badge variant="secondary" className="tabular-nums" aria-label={`${badge} waiting`}>
                          {badge}
                        </Badge>
                      ) : null}
                    </a>
                  </li>
                );
              })}
              {user ? (
                <li className="mt-1 border-t border-border pt-2">
                  <div className="truncate px-3 pb-1 text-xs text-muted-foreground">{user.email}</div>
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      void logout();
                    }}
                    className="flex min-h-12 w-full items-center gap-3 rounded-md px-3 text-left text-sm font-medium text-destructive outline-none hover:bg-destructive/10 focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <LogOut className="size-5" aria-hidden="true" />
                    Sign out
                  </button>
                </li>
              ) : null}
            </ul>
          </SheetContent>
        </Sheet>
      ) : null}
    </nav>
  );
}
