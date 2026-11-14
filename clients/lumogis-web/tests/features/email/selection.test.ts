// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696 — the selection arithmetic. Pure, so every case is exact.

import { describe, expect, it } from "vitest";

import {
  chunk,
  EMPTY_SELECTION,
  prune,
  recoverCursor,
  selectRange,
  setMany,
  toggle,
  type SelectionState,
} from "../../../src/features/email/selection";

const ORDER = ["a", "b", "c", "d", "e"];

const sel = (ids: string[], anchor: string | null = null): SelectionState => ({
  ids: new Set(ids),
  anchor,
});

describe("toggle", () => {
  it("adds an id and makes it the anchor", () => {
    const next = toggle(EMPTY_SELECTION, "b");
    expect([...next.ids]).toEqual(["b"]);
    expect(next.anchor).toBe("b");
  });

  it("removes an already-selected id and still re-anchors", () => {
    const next = toggle(sel(["a", "b"], "a"), "b");
    expect([...next.ids]).toEqual(["a"]);
    expect(next.anchor).toBe("b");
  });
});

describe("selectRange", () => {
  it("covers the inclusive slice when the anchor comes FIRST", () => {
    const next = selectRange(sel(["b"], "b"), ORDER, "d");
    expect([...next.ids].sort()).toEqual(["b", "c", "d"]);
  });

  it("covers the inclusive slice when the anchor comes LAST", () => {
    // The direction that a naive `slice(from, to)` gets wrong.
    const next = selectRange(sel(["d"], "d"), ORDER, "b");
    expect([...next.ids].sort()).toEqual(["b", "c", "d"]);
  });

  it("keeps the anchor put, so a second shift-click re-ranges from the origin", () => {
    const once = selectRange(sel(["b"], "b"), ORDER, "d");
    expect(once.anchor).toBe("b");
    const twice = selectRange(once, ORDER, "e");
    expect([...twice.ids].sort()).toEqual(["b", "c", "d", "e"]);
  });

  it("degrades to a plain toggle with no anchor", () => {
    const next = selectRange(EMPTY_SELECTION, ORDER, "c");
    expect([...next.ids]).toEqual(["c"]);
  });

  it("degrades to a plain toggle when the anchor has left the visible set", () => {
    const next = selectRange(sel(["z"], "z"), ORDER, "c");
    expect([...next.ids].sort()).toEqual(["c", "z"]);
  });

  it("unions with, rather than replaces, an existing selection", () => {
    const next = selectRange(sel(["a", "b"], "b"), ORDER, "c");
    expect([...next.ids].sort()).toEqual(["a", "b", "c"]);
  });
});

describe("setMany", () => {
  it("adds a whole lane", () => {
    expect([...setMany(EMPTY_SELECTION, ["a", "b"], true).ids].sort()).toEqual(["a", "b"]);
  });

  it("removes a whole lane without touching the rest", () => {
    const next = setMany(sel(["a", "b", "c"]), ["a", "b"], false);
    expect([...next.ids]).toEqual(["c"]);
  });
});

describe("prune", () => {
  it("drops ids that left the visible set", () => {
    const next = prune(sel(["a", "zz"], "a"), ORDER);
    expect([...next.ids]).toEqual(["a"]);
  });

  it("clears an anchor that vanished", () => {
    expect(prune(sel(["a"], "zz"), ORDER).anchor).toBeNull();
  });

  it("returns the SAME object when nothing changed (no needless re-render)", () => {
    const before = sel(["a", "b"], "a");
    expect(prune(before, ORDER)).toBe(before);
  });

  it("empties the selection when everything is filtered away", () => {
    expect(prune(sel(["a", "b"], "a"), []).ids.size).toBe(0);
  });
});

describe("chunk", () => {
  it("splits 450 into 200 / 200 / 50", () => {
    const items = Array.from({ length: 450 }, (_, i) => i);
    expect(chunk(items, 200).map((c) => c.length)).toEqual([200, 200, 50]);
  });

  it("returns [] for an empty input", () => {
    expect(chunk([], 200)).toEqual([]);
  });

  it("returns one chunk when the input fits", () => {
    expect(chunk(["a", "b"], 200)).toEqual([["a", "b"]]);
  });

  it("refuses a non-positive size rather than looping forever", () => {
    expect(() => chunk(["a"], 0)).toThrow();
  });
});

describe("recoverCursor", () => {
  it("keeps the cursor when its row is still visible", () => {
    expect(recoverCursor(ORDER, ORDER, "c")).toBe("c");
  });

  it("takes over the FORMER INDEX when the cursor row leaves", () => {
    // The normal case: press `e` on "c", it moves to Done and leaves this lane.
    // The cursor must land on what is now at index 2, i.e. "d".
    expect(recoverCursor(ORDER, ["a", "b", "d", "e"], "c")).toBe("d");
  });

  it("falls back to the last row when the list shrank past the index", () => {
    expect(recoverCursor(ORDER, ["a", "b"], "e")).toBe("b");
  });

  it("returns null when nothing is left", () => {
    expect(recoverCursor(ORDER, [], "c")).toBeNull();
  });

  it("does NOT invent a cursor when there was none", () => {
    // Returning `order[0]` here would move DOM focus on mount — stealing it from
    // wherever the user was and cutting a screen reader off mid-sentence. The
    // cursor is created by user intent (j/k, or clicking a checkbox), not by a
    // render.
    expect(recoverCursor([], ORDER, null)).toBeNull();
  });

  it("falls back to the first row when the cursor row is gone AND was never in the old order", () => {
    expect(recoverCursor([], ORDER, "ghost")).toBe("a");
  });
});
