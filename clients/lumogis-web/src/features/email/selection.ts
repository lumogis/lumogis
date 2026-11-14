// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696: selection arithmetic for the email surface's multi-select.
//
// Deliberately DOM-free and React-free. Every interesting case here is a set
// operation over an ordered list — ranges, pruning, chunking — and they belong
// somewhere a test can reach without rendering anything. `EmailInboxView` holds
// the state; this module only computes the next value of it.

/** The selection, plus the anchor a shift-click extends from. */
export interface SelectionState {
  ids: ReadonlySet<string>;
  anchor: string | null;
}

export const EMPTY_SELECTION: SelectionState = { ids: new Set(), anchor: null };

/** Flip `id`, and make it the new anchor for a subsequent shift-click. */
export function toggle(state: SelectionState, id: string): SelectionState {
  const next = new Set(state.ids);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return { ids: next, anchor: id };
}

/**
 * Union the current selection with the inclusive slice of `order` between the
 * anchor and `id`.
 *
 * Works in both directions (anchor before OR after the click). With no anchor —
 * or an anchor that has since left the visible set — this degrades to a plain
 * `toggle`, because a range from nowhere is not a thing the user asked for.
 *
 * `order` is the FLATTENED rendered order (lanes in render order, then the
 * snoozed disclosure), so a range can span a lane boundary. That matches DOM
 * order and what a shift-select does in any list.
 */
export function selectRange(
  state: SelectionState,
  order: readonly string[],
  id: string,
): SelectionState {
  const from = state.anchor === null ? -1 : order.indexOf(state.anchor);
  const to = order.indexOf(id);
  if (from === -1 || to === -1) return toggle(state, id);
  const [lo, hi] = from <= to ? [from, to] : [to, from];
  const next = new Set(state.ids);
  for (let i = lo; i <= hi; i += 1) next.add(order[i]);
  // The anchor stays put, so shift-clicking again re-ranges from the same
  // origin rather than walking it forward one click at a time.
  return { ids: next, anchor: state.anchor };
}

/** Add or remove many ids at once (a lane's select-all checkbox). */
export function setMany(
  state: SelectionState,
  ids: readonly string[],
  on: boolean,
): SelectionState {
  const next = new Set(state.ids);
  for (const id of ids) {
    if (on) next.add(id);
    else next.delete(id);
  }
  return { ids: next, anchor: state.anchor };
}

/**
 * Intersect the selection with what is currently visible, and drop a vanished
 * anchor.
 *
 * Called whenever the visible set changes — a filter keystroke, a category
 * switch, a refetch. Acting on rows the user can no longer see is the failure
 * mode a bulk action bar must not have: type three characters into the filter
 * box and the count must fall, not stay at 40 and then mark 40 things done.
 */
export function prune(state: SelectionState, order: readonly string[]): SelectionState {
  const visible = new Set(order);
  const next = new Set<string>();
  for (const id of state.ids) if (visible.has(id)) next.add(id);
  const anchor = state.anchor !== null && visible.has(state.anchor) ? state.anchor : null;
  if (next.size === state.ids.size && anchor === state.anchor) return state;
  return { ids: next, anchor };
}

/** Split into batches of at most `size` (the server's per-request id cap). */
export function chunk<T>(items: readonly T[], size: number): T[][] {
  if (size <= 0) throw new Error("chunk size must be positive");
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/**
 * The index the cursor should land on when the row it was pointing at leaves
 * the visible set.
 *
 * This is the NORMAL case during rapid triage, not an edge case: press `e`, the
 * row moves to Done, and if the cursor is not recovered every subsequent
 * keystroke is a silent no-op — the surface feels broken during exactly the
 * flow the feature exists for. The cursor takes over the former index, or the
 * last row if the list shrank past it.
 */
export function recoverCursor(
  previousOrder: readonly string[],
  nextOrder: readonly string[],
  cursorId: string | null,
): string | null {
  // No cursor yet → do NOT invent one. The cursor is created by user intent
  // (j/k, or clicking a checkbox), never by a render: moving DOM focus on mount
  // would steal it from wherever the user was, scroll the viewport, and cut a
  // screen reader off mid-sentence.
  if (cursorId === null) return null;
  if (nextOrder.length === 0) return null;
  if (nextOrder.includes(cursorId)) return cursorId;
  const wasAt = previousOrder.indexOf(cursorId);
  if (wasAt === -1) return nextOrder[0];
  return nextOrder[Math.min(wasAt, nextOrder.length - 1)];
}
