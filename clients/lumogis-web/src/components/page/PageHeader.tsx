// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Every page starts the same way (LUM-993): one title, one line saying what the
// page is for, an optional (i), and at most one main action.
import type { ReactNode, Ref } from "react";

import { InfoTip } from "./InfoTip";

export interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  info?: ReactNode;
  action?: ReactNode;
  /** For pages that move focus to their title (SourcesView, LUM-282). */
  headingRef?: Ref<HTMLHeadingElement>;
  headingTabIndex?: number;
  /** Embedded views render no header of their own (one h1 per page). */
  hidden?: boolean;
}

export function PageHeader({
  title,
  description,
  info,
  action,
  headingRef,
  headingTabIndex,
  hidden = false,
}: PageHeaderProps): JSX.Element | null {
  if (hidden) return null;
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1.5">
        <div className="flex items-center gap-1">
          <h1 ref={headingRef} tabIndex={headingTabIndex} className="m-0 text-2xl font-semibold tracking-tight">
            {title}
          </h1>
          {info ? <InfoTip about={title}>{info}</InfoTip> : null}
        </div>
        {description ? <p className="m-0 max-w-prose text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}
