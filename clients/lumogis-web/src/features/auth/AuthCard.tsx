// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-993 Pass 7. The one centred card the signed-out pages (forgot password, reset password,
// invite, first-run setup) share. Presentation only: each page keeps its own form, token and
// security handling.

import type { JSX, ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface AuthCardProps {
  title: string;
  /** Id for the heading, so a `<form aria-labelledby>` inside can point at it. */
  titleId?: string;
  description?: ReactNode;
  children?: ReactNode;
  /** Props for the outer `<main>` (role="status", data-testid...). */
  mainProps?: React.HTMLAttributes<HTMLElement> & { "data-testid"?: string };
}

export function AuthCard({ title, titleId, description, children, mainProps }: AuthCardProps): JSX.Element {
  return (
    <main className="flex min-h-svh items-start justify-center p-4 pt-12 sm:items-center sm:pt-4" {...mainProps}>
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>
            <h1 id={titleId} className="m-0 text-lg font-semibold">
              {title}
            </h1>
          </CardTitle>
          {description !== undefined ? <CardDescription>{description}</CardDescription> : null}
        </CardHeader>
        {children !== undefined ? <CardContent className="flex flex-col gap-4">{children}</CardContent> : null}
      </Card>
    </main>
  );
}

/** Link styling that passes axe "link-in-text-block". */
export const AUTH_LINK_CLASS = "text-accent-text underline underline-offset-2";
