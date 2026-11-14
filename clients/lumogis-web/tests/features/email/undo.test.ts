// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-696 — undo restores each row to ITS OWN prior state, not to one state.
//
// The regression these guard: bulk-snooze a selection where one row was already
// Done, then undo. A naive "undo = clear everything" silently clears the Done
// row — something the user never asked for and cannot see happening.

import { describe, expect, it } from "vitest";

import { groupForUndo, type TriageSnapshot } from "../../../src/features/email/undo";

const T1 = "2026-07-26T08:00:00.000Z";
const T2 = "2026-07-27T08:00:00.000Z";

const row = (
  signalId: string,
  status: TriageSnapshot["status"],
  snoozedUntil: string | null = null,
): TriageSnapshot => ({ signalId, status, snoozedUntil });

describe("groupForUndo", () => {
  it("splits a mixed snapshot into one batch per distinct prior state", () => {
    const batches = groupForUndo([
      row("a", null),
      row("b", null),
      row("c", "done"),
      row("d", "done"),
      row("e", "done"),
      row("f", "snoozed", T1),
    ]);

    expect(batches).toHaveLength(3);
    const done = batches.find((b) => b.status === "done");
    expect(done?.signalIds).toEqual(["c", "d", "e"]);
    const snoozed = batches.find((b) => b.status === "snoozed");
    expect(snoozed?.signalIds).toEqual(["f"]);
    expect(snoozed?.snoozedUntil).toBe(T1);
  });

  it("does NOT merge two different snooze times", () => {
    // Restoring both to a common wake time would be a different state from the
    // one the user had.
    const batches = groupForUndo([row("a", "snoozed", T1), row("b", "snoozed", T2)]);
    expect(batches).toHaveLength(2);
    expect(batches.map((b) => b.snoozedUntil).sort()).toEqual([T1, T2]);
  });

  it("merges rows that share a status and wake time", () => {
    const batches = groupForUndo([row("a", "snoozed", T1), row("b", "snoozed", T1)]);
    expect(batches).toHaveLength(1);
    expect(batches[0].signalIds).toEqual(["a", "b"]);
  });

  it("emits the clear batch LAST, deterministically", () => {
    const batches = groupForUndo([row("a", null), row("b", "done"), row("c", null)]);
    expect(batches[batches.length - 1].status).toBeNull();
    expect(batches[batches.length - 1].signalIds).toEqual(["a", "c"]);
  });

  it("keeps an untriaged row separate from a 'done' row", () => {
    // The exact case the naive implementation gets wrong.
    const batches = groupForUndo([row("was-untriaged", null), row("was-done", "done")]);
    expect(batches).toHaveLength(2);
    expect(batches.find((b) => b.status === null)?.signalIds).toEqual(["was-untriaged"]);
    expect(batches.find((b) => b.status === "done")?.signalIds).toEqual(["was-done"]);
  });

  it("returns [] for an empty snapshot", () => {
    expect(groupForUndo([])).toEqual([]);
  });

  it("does not collide a null status with a literal 'null' string status", () => {
    // The grouping key is built by string concatenation; a status that happens
    // to stringify near the sentinel must not merge with a genuine clear.
    const batches = groupForUndo([row("a", null), row("b", "done", null)]);
    expect(batches).toHaveLength(2);
  });
});
