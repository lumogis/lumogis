// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-658 — last-known activation cache (removes nav pop-in on cold load).
import { afterEach, describe, expect, it } from "vitest";
import {
  clearCachedEmailActivated,
  isEmailActivated,
  readCachedEmailActivated,
  writeCachedEmailActivated,
} from "../../../src/features/email/activation";
import type { SourceRow } from "../../../src/api/emailSources";

function src(source_type: string): SourceRow {
  return {
    id: "s", name: "n", source_type, url: "u", category: "email", active: true,
    poll_interval: 900, last_polled_at: null, last_signal_at: null, polling_active: true,
  };
}

afterEach(() => {
  try { globalThis.localStorage?.clear(); } catch { /* ignore */ }
});

describe("isEmailActivated", () => {
  it("true iff an imap source is present", () => {
    expect(isEmailActivated([src("imap")])).toBe(true);
    expect(isEmailActivated([src("rss")])).toBe(false);
    expect(isEmailActivated([])).toBe(false);
    expect(isEmailActivated(undefined)).toBe(false);
  });
});

describe("activation cache (LUM-658)", () => {
  it("round-trips true/false", () => {
    writeCachedEmailActivated(true);
    expect(readCachedEmailActivated()).toBe(true);
    writeCachedEmailActivated(false);
    expect(readCachedEmailActivated()).toBe(false);
  });

  it("fails closed when nothing cached", () => {
    expect(readCachedEmailActivated()).toBe(false);
  });
});

describe("clearCachedEmailActivated (LUM-886)", () => {
  it("removes the hint; readCachedEmailActivated() then returns false", () => {
    writeCachedEmailActivated(true);
    expect(readCachedEmailActivated()).toBe(true);
    clearCachedEmailActivated();
    expect(readCachedEmailActivated()).toBe(false);
    expect(localStorage.getItem("lumogis:email:activated")).toBeNull();
  });
});
