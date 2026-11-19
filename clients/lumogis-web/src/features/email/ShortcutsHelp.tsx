// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696: the `?` overlay. Renders from the SAME `SHORTCUTS` table the hook
// binds from, so a new shortcut cannot ship undocumented — the test asserts the
// two agree by construction rather than by a hand-maintained list.
//
// LUM-993: now the shared Dialog. It owns Escape, the focus trap and putting
// focus back where it was (so `?` then Esc does not dump the keyboard cursor).

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { SHORTCUTS } from "./useTriageKeyboard";

export interface ShortcutsHelpProps {
  open: boolean;
  onClose: () => void;
}

export function ShortcutsHelp({ open, onClose }: ShortcutsHelpProps): JSX.Element {
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent data-testid="shortcuts-help" aria-label="Keyboard shortcuts">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Work through your email without the mouse.</DialogDescription>
        </DialogHeader>
        <dl className="m-0 grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2">
          {SHORTCUTS.map((s) => (
            <div key={s.keys} className="contents">
              <dt>
                <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-xs">{s.keys}</kbd>
              </dt>
              <dd className="m-0 text-muted-foreground">{s.label}</dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  );
}
