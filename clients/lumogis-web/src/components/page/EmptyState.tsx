// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Empty state for any list or panel (LUM-993 page kit): an icon, one sentence,
// at most a couple of actions. Same props as the LUM-165 component it replaces,
// which now re-exports this one, so callers are unchanged.

import { type ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn } from "cn";

import { Button } from "../Button";
import { legacyButtonClasses } from "../buttonClasses";

export interface EmptyStateAction {
  label: string;
  onClick?: () => void;
  href?: string;
  primary?: boolean;
}

export interface EmptyStateProps {
  title: string;
  helperText?: string;
  icon?: ReactNode;
  children?: ReactNode;
  actions?: EmptyStateAction[];
  /** Merged with the root layout class (e.g. a border for a compact card). */
  className?: string;
}

export function EmptyState({
  title,
  helperText,
  icon,
  children,
  actions,
  className,
}: EmptyStateProps): JSX.Element {
  const primaryIdx = actions?.findIndex((a) => a.primary) ?? -1;

  return (
    <div
      className={cn(
        "mx-auto flex max-w-md flex-col items-center gap-3 px-4 py-10 text-center",
        className,
      )}
    >
      {icon !== undefined && icon !== null ? (
        <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground [&_svg]:size-5">
          {icon}
        </div>
      ) : null}
      <h2 className="m-0 text-base font-semibold">{title}</h2>
      {helperText !== undefined && helperText.length > 0 ? (
        <p className="m-0 text-sm text-muted-foreground">{helperText}</p>
      ) : null}
      {children}
      {actions !== undefined && actions.length > 0 ? (
        <div className="mt-1 flex flex-wrap justify-center gap-2">
          {actions.map((a, i) => {
            const variant = primaryIdx === i ? "primary" : "secondary";
            const key = `${a.label}-${i}`;
            if (a.href !== undefined && a.href.length > 0) {
              const cls = legacyButtonClasses(variant, "md");
              if (/^https?:\/\//i.test(a.href)) {
                return (
                  <a key={key} className={cls} href={a.href} target="_blank" rel="noopener noreferrer">
                    {a.label}
                  </a>
                );
              }
              return (
                <Link key={key} className={cls} to={a.href}>
                  {a.label}
                </Link>
              );
            }
            return (
              <Button key={key} variant={variant} onClick={a.onClick}>
                {a.label}
              </Button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
