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
      className="lumogis-email__bulk-bar"
      data-testid="bulk-action-bar"
      role="region"
      aria-label="Bulk actions"
    >
      <span className="lumogis-email__bulk-count" role="status">
        {count} selected
      </span>
      <button type="button" disabled={busy} onClick={onDone}>
        Mark done
      </button>
      <button
        type="button"
        disabled={busy}
        aria-expanded={snoozeOpen}
        onClick={onToggleSnooze}
      >
        Snooze
      </button>
      {snoozeOpen ? (
        <ul className="lumogis-email__bulk-snooze-menu" data-testid="bulk-snooze-menu">
          {SNOOZE_PRESETS.map((preset, i) => (
            <li key={preset.label}>
              <button
                type="button"
                disabled={busy}
                // One wake time for the whole batch, computed ONCE here — not
                // per row, which would land 200 rows on 200 timestamps a
                // microsecond apart.
                onClick={() => onSnooze(preset.at().toISOString())}
              >
                <kbd>{i + 1}</kbd> {preset.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <button type="button" disabled={busy} onClick={onDeselectAll}>
        Clear selection
      </button>
    </div>
  );
}
