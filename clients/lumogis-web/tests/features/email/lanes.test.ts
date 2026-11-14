// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-642 — provisional lane derivation.
import { describe, expect, it } from "vitest";
import { deriveLane, NEEDS_YOU_IMPORTANCE } from "../../../src/features/email/lanes";
import type { EmailSignalRow } from "../../../src/api/emailSources";

function row(overrides: Partial<EmailSignalRow> = {}): EmailSignalRow {
  return {
    signal_id: "s1",
    source_id: "src",
    title: "t",
    url: "imap://src/1/1",
    category: "",
    category_label: "",
    scope: "personal",
    published_at: null,
    content_summary: "",
    ...overrides,
  };
}

describe("deriveLane", () => {
  it("notified=true → needs_you", () => {
    expect(deriveLane(row({ notified: true, importance_score: 0 }))).toBe("needs_you");
  });

  it("importance ≥ threshold → needs_you (no profile required)", () => {
    expect(deriveLane(row({ notified: false, importance_score: NEEDS_YOU_IMPORTANCE }))).toBe("needs_you");
  });

  it("not notified and low importance → good_to_know", () => {
    expect(deriveLane(row({ notified: false, importance_score: 0.2 }))).toBe("good_to_know");
  });

  it("absent fields → good_to_know (absent-safe, never done in v1)", () => {
    expect(deriveLane(row())).toBe("good_to_know");
  });

  // LUM-649: the learned nudge is added to importance before the threshold.
  it("positive learned_relevance_delta pushes a sub-threshold signal into needs_you", () => {
    expect(
      deriveLane(row({ notified: false, importance_score: 0.5, learned_relevance_delta: 0.2 })),
    ).toBe("needs_you");
  });

  it("negative learned_relevance_delta keeps a borderline signal in good_to_know", () => {
    expect(
      deriveLane(row({ notified: false, importance_score: 0.65, learned_relevance_delta: -0.2 })),
    ).toBe("good_to_know");
  });

  it("absent learned_relevance_delta is a no-op (older backend safe)", () => {
    expect(deriveLane(row({ notified: false, importance_score: NEEDS_YOU_IMPORTANCE }))).toBe(
      "needs_you",
    );
  });
});
