// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-643 — effectiveLane: real triage state wins; snooze re-surfaces at query
// time; untriaged falls back to the provisional deriveLane derivation.
import { describe, expect, it } from "vitest";
import {
  collapseProjections,
  effectiveLane,
  isSnoozedHidden,
  SNOOZE_PRESETS,
} from "../../../src/features/email/triage";
import type { EmailSignalRow } from "../../../src/api/emailSources";

function row(overrides: Partial<EmailSignalRow> = {}): EmailSignalRow {
  return {
    signal_id: "s1",
    source_id: "src",
    title: "Title",
    url: "imap://src/1/1",
    category: "",
    category_label: "",
    scope: "personal",
    published_at: "2026-07-20T09:00:00Z",
    content_summary: "",
    ...overrides,
  };
}

const NOW = new Date("2026-07-21T12:00:00Z");

describe("effectiveLane", () => {
  it("done → done", () => {
    expect(effectiveLane(row({ triage_status: "done" }), NOW)).toBe("done");
  });

  it("explicit needs_you / good_to_know → that lane", () => {
    expect(effectiveLane(row({ triage_status: "needs_you" }), NOW)).toBe("needs_you");
    expect(effectiveLane(row({ triage_status: "good_to_know" }), NOW)).toBe("good_to_know");
  });

  it("snoozed with a future wake time → hidden (Snoozed disclosure)", () => {
    const future = new Date(NOW.getTime() + 3600_000).toISOString();
    expect(effectiveLane(row({ triage_status: "snoozed", snoozed_until: future }), NOW)).toBe(
      "snoozed_hidden",
    );
  });

  it("snoozed but past its wake time → re-surfaces to needs_you", () => {
    const past = new Date(NOW.getTime() - 3600_000).toISOString();
    expect(effectiveLane(row({ triage_status: "snoozed", snoozed_until: past }), NOW)).toBe(
      "needs_you",
    );
  });

  it("snoozed with a malformed/absent wake time → re-surfaces (never trapped hidden)", () => {
    expect(effectiveLane(row({ triage_status: "snoozed", snoozed_until: null }), NOW)).toBe(
      "needs_you",
    );
  });

  it("untriaged → default derivation (notified ∨ importance ≥ 0.6)", () => {
    expect(effectiveLane(row({ triage_status: null, notified: true }), NOW)).toBe("needs_you");
    expect(effectiveLane(row({ importance_score: 0.9 }), NOW)).toBe("needs_you");
    expect(effectiveLane(row({ importance_score: 0.1, notified: false }), NOW)).toBe(
      "good_to_know",
    );
  });

  // LUM-649: the learned nudge reaches the fallback path effectiveLane uses (this
  // is the function EmailInboxView actually calls — deriveLane is only its fallback).
  it("untriaged: positive learned_relevance_delta crosses into needs_you via the fallback", () => {
    expect(
      effectiveLane(
        row({ triage_status: null, importance_score: 0.5, learned_relevance_delta: 0.2 }),
        NOW,
      ),
    ).toBe("needs_you");
  });

  it("a durable triage_status still wins over the learned nudge", () => {
    // Even a strong positive delta cannot override an explicit good_to_know.
    expect(
      effectiveLane(
        row({ triage_status: "good_to_know", importance_score: 0.9, learned_relevance_delta: 0.6 }),
        NOW,
      ),
    ).toBe("good_to_know");
  });

  it("isSnoozedHidden matches the hidden bucket", () => {
    const future = new Date(NOW.getTime() + 3600_000).toISOString();
    expect(isSnoozedHidden(row({ triage_status: "snoozed", snoozed_until: future }), NOW)).toBe(
      true,
    );
    expect(isSnoozedHidden(row({ triage_status: "done" }), NOW)).toBe(false);
  });
});

describe("collapseProjections (LUM-661)", () => {
  it("collapses an owner's personal original + its shared projection into one row", () => {
    const original = row({ signal_id: "X", scope: "personal" });
    const projection = row({ signal_id: "Y", scope: "shared", published_from: "X" });
    const out = collapseProjections([original, projection]);
    expect(out).toHaveLength(1);
    expect(out[0].signal_id).toBe("X"); // prefers the personal original when neither triaged
  });

  it("prefers the triaged member so triage on either copy surfaces", () => {
    const original = row({ signal_id: "X", scope: "personal", triage_status: null });
    const projection = row({ signal_id: "Y", scope: "shared", published_from: "X", triage_status: "done" });
    const out = collapseProjections([original, projection]);
    expect(out).toHaveLength(1);
    expect(out[0].signal_id).toBe("Y");
    expect(out[0].triage_status).toBe("done");
  });

  it("leaves a non-owner's lone projection untouched (no original visible)", () => {
    const projection = row({ signal_id: "Y", scope: "shared", published_from: "X" });
    const out = collapseProjections([projection]);
    expect(out).toEqual([projection]);
  });

  it("does not merge unrelated signals and preserves order", () => {
    const a = row({ signal_id: "A" });
    const b = row({ signal_id: "B" });
    const out = collapseProjections([a, b]);
    expect(out.map((s) => s.signal_id)).toEqual(["A", "B"]);
  });
});

describe("SNOOZE_PRESETS", () => {
  it("every preset resolves to a strictly-future time", () => {
    expect(SNOOZE_PRESETS.length).toBeGreaterThan(0);
    for (const preset of SNOOZE_PRESETS) {
      expect(preset.at(NOW).getTime()).toBeGreaterThan(NOW.getTime());
    }
  });
});
