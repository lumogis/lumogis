// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696: the bar that appears once something is selected.
//
// Presentational — it holds no selection state and issues no requests. There are
// exactly TWO bulk data actions here, mark-done and snooze. `onDeselectAll` is
// UI-only and deliberately named so: a prop called `onClear` sitting beside
// `onDone`/`onSnooze` reads like a third mutating action, and the bulk *clear*
// SQL exists only so undo can replay a "was untriaged" batch — it is never
// reachable from a button.

import { Button } from "@/components/ui/button";

import { SNOOZE_PRESETS } from "./triage";

export interface BulkActionBarProps {
  count: number;
  busy?: boolean;
  onDone: () => void;
  onSnooze: (isoWakeTime: string) => void;
  /** UI-only: empties the selection. Issues no request. */
  onDeselectAll: () => void;
  snoozeOpen: boolean;
  onToggleSnooze: () => void;
}

export function BulkActionBar({
  count,
  busy = false,
  onDone,
  onSnooze,
  onDeselectAll,
  snoozeOpen,
  onToggleSnooze,
}: BulkActionBarProps): JSX.Element | null {
  if (count === 0) return null;
  return (
    <div
      className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/50 p-2"
      data-testid="bulk-action-bar"
      role="region"
      aria-label="Bulk actions"
    >
      <span className="px-1 text-sm font-medium" role="status">
        {count} selected
      </span>
      <Button type="button" size="sm" disabled={busy} onClick={onDone}>
        Mark done
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        aria-expanded={snoozeOpen}
        onClick={onToggleSnooze}
      >
        Snooze
      </Button>
      <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onDeselectAll}>
        Clear selection
      </Button>
      {snoozeOpen ? (
        <ul
          className="m-0 flex w-full list-none flex-wrap gap-2 p-0"
          data-testid="bulk-snooze-menu"
        >
          {SNOOZE_PRESETS.map((preset, i) => (
            <li key={preset.label}>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                // One wake time for the whole batch, computed ONCE here — not
                // per row, which would land 200 rows on 200 timestamps a
                // microsecond apart.
                onClick={() => onSnooze(preset.at().toISOString())}
              >
                <kbd className="rounded border bg-muted px-1 font-mono text-xs">{i + 1}</kbd> {preset.label}
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
