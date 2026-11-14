// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696 — the keyboard layer.
//
// The refusal cases below are the load-bearing half. "Does `e` mark done" is
// easy to get right; "does `e` stay out of the way while you are typing, and
// does Cmd-R still reload" is where the bugs are, and a shortcut layer that
// swallows browser keys is a bug users cannot work around.

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  isTypingTarget,
  SHORTCUTS,
  useTriageKeyboard,
  type TriageKeyHandlers,
} from "../../../src/features/email/useTriageKeyboard";

function makeHandlers(): TriageKeyHandlers & Record<string, ReturnType<typeof vi.fn>> {
  return {
    moveCursor: vi.fn(),
    toggleSelect: vi.fn(),
    extendSelect: vi.fn(),
    clearSelection: vi.fn(),
    markDone: vi.fn(),
    openSnooze: vi.fn(),
    snoozePreset: vi.fn(),
    undo: vi.fn(),
    reply: vi.fn(),
    forward: vi.fn(),
    focusFilter: vi.fn(),
    toggleHelp: vi.fn(),
  } as never;
}

let handlers: ReturnType<typeof makeHandlers>;

function Harness({
  enabled = true,
  snoozeMenuOpen = false,
}: {
  enabled?: boolean;
  snoozeMenuOpen?: boolean;
}) {
  useTriageKeyboard(handlers, { enabled, snoozeMenuOpen });
  return (
    <div>
      <input data-testid="text" type="text" />
      <textarea data-testid="area" />
      <input data-testid="check" type="checkbox" />
      <div data-testid="editable" contentEditable suppressContentEditableWarning />
    </div>
  );
}

function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = document) {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(ev);
  return ev;
}

beforeEach(() => {
  handlers = makeHandlers();
});

describe("bindings", () => {
  it.each([
    ["j", "moveCursor"],
    ["ArrowDown", "moveCursor"],
    ["k", "moveCursor"],
    ["ArrowUp", "moveCursor"],
    ["x", "toggleSelect"],
    ["X", "extendSelect"],
    ["e", "markDone"],
    ["s", "openSnooze"],
    ["u", "undo"],
    ["r", "reply"],
    ["f", "forward"],
    ["/", "focusFilter"],
    ["?", "toggleHelp"],
    ["Escape", "clearSelection"],
  ])("%s fires %s", (key, handler) => {
    render(<Harness />);
    press(key);
    expect(handlers[handler]).toHaveBeenCalled();
  });

  it("j moves forward and k moves back", () => {
    render(<Harness />);
    press("j");
    expect(handlers.moveCursor).toHaveBeenCalledWith(1);
    press("k");
    expect(handlers.moveCursor).toHaveBeenCalledWith(-1);
  });
});

describe("the snooze preset keys are gated on the menu", () => {
  it("1-5 do nothing while the menu is closed", () => {
    render(<Harness snoozeMenuOpen={false} />);
    for (const k of ["1", "2", "3", "4", "5"]) press(k);
    expect(handlers.snoozePreset).not.toHaveBeenCalled();
  });

  it("1-5 pick a preset while the menu is open", () => {
    render(<Harness snoozeMenuOpen />);
    press("3");
    expect(handlers.snoozePreset).toHaveBeenCalledWith(2);
  });
});

describe("refusals — the half where the bugs live", () => {
  it.each([["text"], ["area"], ["editable"]])(
    "no handler fires while the user is typing in %s",
    (testid) => {
      const { getByTestId } = render(<Harness />);
      press("e", {}, getByTestId(testid));
      press("j", {}, getByTestId(testid));
      expect(handlers.markDone).not.toHaveBeenCalled();
      expect(handlers.moveCursor).not.toHaveBeenCalled();
    },
  );

  it("STILL fires when focus is on a checkbox — that is where our cursor lives", () => {
    const { getByTestId } = render(<Harness />);
    press("e", {}, getByTestId("check"));
    expect(handlers.markDone).toHaveBeenCalled();
  });

  it.each([["metaKey"], ["ctrlKey"], ["altKey"]])(
    "%s is never swallowed — Cmd-R must still reload",
    (mod) => {
      render(<Harness />);
      const ev = press("r", { [mod]: true } as KeyboardEventInit);
      expect(handlers.reply).not.toHaveBeenCalled();
      expect(ev.defaultPrevented).toBe(false);
    },
  );

  it("shiftKey IS honoured — ? is Shift+/ and X is Shift+x", () => {
    render(<Harness />);
    press("?", { shiftKey: true });
    press("X", { shiftKey: true });
    expect(handlers.toggleHelp).toHaveBeenCalled();
    expect(handlers.extendSelect).toHaveBeenCalled();
  });

  it("nothing fires while disabled (a composer owns the page)", () => {
    render(<Harness enabled={false} />);
    for (const k of ["e", "j", "x", "u", "?"]) press(k);
    expect(handlers.markDone).not.toHaveBeenCalled();
    expect(handlers.moveCursor).not.toHaveBeenCalled();
    expect(handlers.toggleHelp).not.toHaveBeenCalled();
  });

  it("removes its listener on unmount", () => {
    const { unmount } = render(<Harness />);
    unmount();
    press("e");
    expect(handlers.markDone).not.toHaveBeenCalled();
  });

  it("an unbound key does nothing and is not prevented", () => {
    render(<Harness />);
    const ev = press("q");
    expect(ev.defaultPrevented).toBe(false);
  });
});

describe("isTypingTarget", () => {
  it("is false for null and for a checkbox", () => {
    expect(isTypingTarget(null)).toBe(false);
    const cb = document.createElement("input");
    cb.type = "checkbox";
    expect(isTypingTarget(cb)).toBe(false);
  });

  it("is true for text inputs, textareas, selects and contenteditable", () => {
    const text = document.createElement("input");
    text.type = "text";
    expect(isTypingTarget(text)).toBe(true);
    expect(isTypingTarget(document.createElement("textarea"))).toBe(true);
    expect(isTypingTarget(document.createElement("select"))).toBe(true);
    const div = document.createElement("div");
    Object.defineProperty(div, "isContentEditable", { value: true });
    expect(isTypingTarget(div)).toBe(true);
  });
});

describe("the shortcuts table is the single source", () => {
  it("documents every key the hook binds", () => {
    // Driven off the exported table the overlay also renders, so a new shortcut
    // cannot ship undocumented.
    const documented = SHORTCUTS.map((s) => s.keys).join(" ");
    for (const key of ["j", "k", "x", "X", "e", "s", "u", "r", "f", "/", "Esc", "?"]) {
      expect(documented).toContain(key);
    }
    expect(SHORTCUTS.every((s) => s.label.length > 0)).toBe(true);
  });
});
