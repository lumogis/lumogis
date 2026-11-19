// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Error state (LUM-211 principles, LUM-993 look): never a raw error body, always
// an action, one plain sentence. The hint used to tell people to run a shell
// command ("lumogis doctor"); household members cannot, so it now points at the
// Health page an admin can open. The prop keeps its name so callers are unchanged.

import { TriangleAlert } from "lucide-react";

import { cn } from "cn";

import { help } from "@/copy/help";

import { EmptyState, type EmptyStateAction } from "./EmptyState";

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
  /** Show the "if this keeps happening" hint for technical or service failures. */
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
    <div role="alert" className={cn("w-full", className)}>
      <EmptyState
        title={title}
        helperText={message}
        icon={<TriangleAlert className="text-destructive" aria-hidden="true" />}
        actions={allActions}
      />
      {doctorHint ? (
        <p className="m-0 -mt-6 pb-6 text-center text-xs text-muted-foreground">{help.errors.persistent}</p>
      ) : null}
    </div>
  );
}
