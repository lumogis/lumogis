// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696: the `?` overlay. Renders from the SAME `SHORTCUTS` table the hook
// binds from, so a new shortcut cannot ship undocumented — the test asserts the
// two agree by construction rather than by a hand-maintained list.

import { useEffect, useRef } from "react";

import { SHORTCUTS } from "./useTriageKeyboard";

export interface ShortcutsHelpProps {
  open: boolean;
  onClose: () => void;
}

export function ShortcutsHelp({ open, onClose }: ShortcutsHelpProps): JSX.Element | null {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const restoreRef = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    // Remember where focus was so closing puts it back — otherwise `?` then Esc
    // dumps the user at the top of the document and the keyboard cursor is lost,
    // which is a strange thing for a keyboard-shortcuts overlay to do.
    restoreRef.current = document.activeElement;
    closeRef.current?.focus();
    return () => {
      const prev = restoreRef.current;
      if (prev instanceof HTMLElement) prev.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="lumogis-email__shortcuts"
      data-testid="shortcuts-help"
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <h3>Keyboard shortcuts</h3>
      <dl>
        {SHORTCUTS.map((s) => (
          <div key={s.keys}>
            <dt>
              <kbd>{s.keys}</kbd>
            </dt>
            <dd>{s.label}</dd>
          </div>
        ))}
      </dl>
      <button type="button" ref={closeRef} onClick={onClose}>
        Close
      </button>
    </div>
  );
}
