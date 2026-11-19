// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// A titled block of related settings or content (LUM-993).
import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { InfoTip } from "./InfoTip";

export function PageSection({
  title,
  description,
  info,
  children,
}: {
  title: string;
  description?: ReactNode;
  info?: ReactNode;
  children: ReactNode;
}): JSX.Element {
  return (
    <Card className="mb-4 max-w-2xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-1 text-base">
          <h2 className="m-0 text-base font-semibold">{title}</h2>
          {info ? <InfoTip about={title}>{info}</InfoTip> : null}
        </CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/** A label/value row inside a PageSection. */
export function Row({ label, info, children }: { label: string; info?: ReactNode; children: ReactNode }): JSX.Element {
  return (
    <div className="flex min-h-11 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b py-2 last:border-b-0">
      <dt className="flex items-center gap-1 text-sm text-muted-foreground">
        {label}
        {info ? <InfoTip about={label}>{info}</InfoTip> : null}
      </dt>
      <dd className="m-0 min-w-0 text-sm font-medium break-words">{children}</dd>
    </div>
  );
}
