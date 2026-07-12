// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button } from "./Button";

export interface RowActionsMenuProps {
  /** Primary inline actions (2–3 buttons). */
  primary: ReactNode;
  /** Overflow actions shown in the ⋯ menu. */
  overflow: ReactNode;
  /** Accessible label for the menu trigger, e.g. "More actions for user@home.lan". */
  menuLabel: string;
  testId?: string;
}

export function RowActionsMenu({
  primary,
  overflow,
  menuLabel,
  testId,
}: RowActionsMenuProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="lumogis-table-actions" ref={rootRef}>
      <div className="lumogis-table-actions__primary">{primary}</div>
      <div className="lumogis-table-actions__menu">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="lumogis-table-actions__trigger"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={menuId}
          aria-label={menuLabel}
          data-testid={testId}
          onClick={() => setOpen((v) => !v)}
        >
          ⋯ More
        </Button>
        {open ? (
          <div className="lumogis-table-actions__panel" id={menuId} role="menu">
            {overflow}
          </div>
        ) : null}
      </div>
    </div>
  );
}
