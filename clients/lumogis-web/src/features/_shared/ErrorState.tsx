// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Shared error-state UI (LUM-211). Design principles: never show a raw 500 /
// stack trace / JSON; always offer at least one action; explain the cause in
// one plain sentence; point technical failures at `lumogis doctor`. Composes
// EmptyState for consistent button styling and wraps it in a role="alert"
// region so assistive tech announces the failure.

import { EmptyState, type EmptyStateAction } from "./EmptyState";
import styles from "./ErrorState.module.css";

export interface ErrorStateProps {
  /** Short, plain-language headline. Defaults to a generic message. */
  title?: string;
  /** One-sentence explanation in plain language (never a raw error body). */
  message: string;
  /** When provided, renders a primary "Try again" action. */
  onRetry?: () => void;
  retryLabel?: string;
  /** Extra actions (rendered after Retry). */
  actions?: EmptyStateAction[];
  /** Show the "Run lumogis doctor" hint for technical/service failures. */
  doctorHint?: boolean;
  className?: string;
}

export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  retryLabel = "Try again",
  actions = [],
  doctorHint = true,
  className,
}: ErrorStateProps): JSX.Element {
  const allActions: EmptyStateAction[] = [
    ...(onRetry ? [{ label: retryLabel, onClick: onRetry, primary: true }] : []),
    ...actions,
  ];
  return (
    <div role="alert" className={[styles.root, className].filter(Boolean).join(" ")}>
      <EmptyState title={title} helperText={message} icon={<span aria-hidden="true">⚠️</span>} actions={allActions} />
      {doctorHint ? (
        <p className={styles.doctorHint}>
          Still stuck? Run <code>lumogis doctor</code> to check your services.
        </p>
      ) : null}
    </div>
  );
}
