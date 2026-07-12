// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Shared empty / zero-state layout for Lumogis Web (LUM-165; LUM-160/LUM-161 reuse).

import { type ReactNode } from "react";
import { Link } from "react-router-dom";

import { buttonClassName } from "../../components/Button";
import styles from "./EmptyState.module.css";

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
  /** Merged with the root layout class (e.g. ``lumogis-chat__empty``). */
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
  const rootClass = [styles.root, className].filter(Boolean).join(" ");
  const primaryIdx = actions?.findIndex((a) => a.primary) ?? -1;

  return (
    <div className={rootClass}>
      {icon !== undefined && icon !== null ? <div className={styles.icon}>{icon}</div> : null}
      <h2 className={styles.title}>{title}</h2>
      {helperText !== undefined && helperText.length > 0 ? (
        <p className={styles.helper}>{helperText}</p>
      ) : null}
      {children}
      {actions !== undefined && actions.length > 0 ? (
        <div className={styles.actions}>
          {actions.map((a, i) => {
            const isPrimary = primaryIdx === i;
            const btnClass = buttonClassName(isPrimary ? "primary" : "secondary", "md");
            if (a.href !== undefined && a.href.length > 0) {
              const external = /^https?:\/\//i.test(a.href);
              if (external) {
                return (
                  <a
                    key={`${a.label}-${i}`}
                    className={btnClass}
                    href={a.href}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {a.label}
                  </a>
                );
              }
              return (
                <Link key={`${a.label}-${i}`} className={btnClass} to={a.href}>
                  {a.label}
                </Link>
              );
            }
            return (
              <button key={`${a.label}-${i}`} type="button" className={btnClass} onClick={a.onClick}>
                {a.label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
