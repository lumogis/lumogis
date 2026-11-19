// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Small pieces the credential forms share (LUM-993 plan B Pass 5): a label above
// its control, with an optional note under it, and the inline error. Nothing
// here changes what a form sends.
import { useId, type ReactNode } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";

/** A label above one control. `children` receives the id to put on the control. */
export function Field({
  label,
  note,
  children,
}: {
  label: string;
  /** Text under the control; wired to it with aria-describedby by the caller via `noteId`. */
  note?: ReactNode;
  children: (ids: { id: string; noteId: string | undefined }) => ReactNode;
}): JSX.Element {
  const id = useId();
  const noteId = note ? `${id}-note` : undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children({ id, noteId })}
      {note ? (
        <p id={noteId} className="m-0 text-xs text-muted-foreground">
          {note}
        </p>
      ) : null}
    </div>
  );
}

/** The inline error under a form. */
export function FormError({ children }: { children: ReactNode }): JSX.Element {
  return (
    <Alert variant="destructive">
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}
