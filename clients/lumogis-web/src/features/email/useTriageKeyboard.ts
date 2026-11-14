// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696: single-key triage shortcuts for the email surface.
//
// ONE document-level keydown listener for the whole view, not one per card —
// with 200 cards rendered, per-card listeners would be 200 registrations on
// every re-render.
//
// Most of the value in this module is in what it REFUSES to do. "Does `e` mark
// done" is easy to get right and easy to test; "does `e` stay out of the way
// while you are typing in the filter box, and does Cmd-R still reload" is where
// the bugs live, and those are the cases the tests lean on.

import { useEffect } from "react";

/** The one table the hook binds from AND the help overlay renders from, so a
 *  shortcut cannot ship undocumented. */
export const SHORTCUTS: ReadonlyArray<{ keys: string; label: string }> = [
  { keys: "j / ↓", label: "Next email" },
  { keys: "k / ↑", label: "Previous email" },
  { keys: "x", label: "Select / deselect" },
  { keys: "X", label: "Extend selection to here" },
  { keys: "e", label: "Mark done" },
  { keys: "s", label: "Snooze…" },
  { keys: "1 – 5", label: "Pick a snooze time (while the menu is open)" },
  { keys: "u", label: "Undo the last triage action" },
  { keys: "r", label: "Reply" },
  { keys: "f", label: "Forward" },
  { keys: "/", label: "Jump to the filter box" },
  { keys: "Esc", label: "Clear selection / close" },
  { keys: "?", label: "Show this help" },
];

export interface TriageKeyHandlers {
  moveCursor: (delta: 1 | -1) => void;
  toggleSelect: () => void;
  extendSelect: () => void;
  clearSelection: () => void;
  markDone: () => void;
  openSnooze: () => void;
  snoozePreset: (index: number) => void;
  undo: () => void;
  reply: () => void;
  forward: () => void;
  focusFilter: () => void;
  toggleHelp: () => void;
}

export interface TriageKeyOptions {
  /** False while a composer or dialog owns the page. */
  enabled: boolean;
  /** Gates the 1–5 preset keys. */
  snoozeMenuOpen: boolean;
}

/** True when the event target is somewhere the user is typing. */
export function isTypingTarget(el: EventTarget | null): boolean {
  if (el === null || typeof el !== "object") return false;
  const node = el as HTMLElement;
  const tag = (node.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    // A checkbox is not a typing target — it is where our own keyboard cursor
    // lives, so `j`/`k`/`e` must keep working while it holds focus.
    if (tag === "INPUT") {
      const type = ((node as HTMLInputElement).type || "").toLowerCase();
      if (type === "checkbox" || type === "radio" || type === "button") return false;
    }
    return true;
  }
  // Two checks, not one. `isContentEditable` is the correct property and it is
  // INHERITED, so it catches a <span> nested inside an editable region — but
  // jsdom does not implement it, so a test would pass while the real guard is
  // untested. The attribute lookup via closest() works in both and independently
  // covers the nesting case. Either alone is a hole.
  if (node.isContentEditable === true) return true;
  return (
    typeof node.closest === "function" &&
    node.closest('[contenteditable]:not([contenteditable="false"])') !== null
  );
}

export function useTriageKeyboard(
  handlers: TriageKeyHandlers,
  { enabled, snoozeMenuOpen }: TriageKeyOptions,
): void {
  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      // Reject meta/ctrl/alt so Cmd-R still reloads and Ctrl-E still reaches the
      // browser. shiftKey is deliberately ALLOWED — `?` is Shift+/ and `X` is
      // Shift+x, so a blanket "no modifiers" guard would silently disable the
      // help overlay and range-extend.
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;

      const key = event.key;

      if (snoozeMenuOpen && key >= "1" && key <= "5") {
        event.preventDefault();
        handlers.snoozePreset(Number(key) - 1);
        return;
      }

      switch (key) {
        case "j":
        case "ArrowDown":
          event.preventDefault();
          handlers.moveCursor(1);
          return;
        case "k":
        case "ArrowUp":
          event.preventDefault();
          handlers.moveCursor(-1);
          return;
        case "x":
          event.preventDefault();
          handlers.toggleSelect();
          return;
        case "X":
          event.preventDefault();
          handlers.extendSelect();
          return;
        case "e":
          event.preventDefault();
          handlers.markDone();
          return;
        case "s":
          event.preventDefault();
          handlers.openSnooze();
          return;
        case "u":
          event.preventDefault();
          handlers.undo();
          return;
        case "r":
          event.preventDefault();
          handlers.reply();
          return;
        case "f":
          event.preventDefault();
          handlers.forward();
          return;
        case "/":
          event.preventDefault();
          handlers.focusFilter();
          return;
        case "?":
          event.preventDefault();
          handlers.toggleHelp();
          return;
        case "Escape":
          handlers.clearSelection();
          return;
        default:
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [enabled, snoozeMenuOpen, handlers]);
}
