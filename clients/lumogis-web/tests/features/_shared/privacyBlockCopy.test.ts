// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-881 — the copy table. Cases are keyed on named `/me/privacy-mode`
 * payloads and carry the expected table ROW NUMBER, so a wrong selection rule
 * fails on the row even if the strings were copied from the implementation.
 */
import { describe, expect, it } from "vitest";

import type { ModelDescriptor } from "../../../src/api/models";
import type { MePrivacyModeResponse } from "../../../src/api/privacyMode";
import {
  catalogHasPrivacyBlock,
  privacyBlockCopy,
  type PrivacyHintCopy,
  type PrivacyHintSurface,
} from "../../../src/features/_shared/privacyBlockCopy";

function me(
  instance: "local_only" | "allow_cloud",
  opts: { locked?: boolean; restriction?: "inherit" | "local_only" } = {},
): MePrivacyModeResponse {
  const restriction = opts.restriction ?? "inherit";
  return {
    instance: {
      privacy_mode: instance,
      privacy_mode_locked: opts.locked ?? false,
      privacy_effective: instance,
    },
    user_restriction: restriction,
    privacy_effective: instance === "local_only" || restriction === "local_only" ? "local_only" : "allow_cloud",
    can_allow_cloud: instance === "allow_cloud",
  };
}

const PAYLOADS: Record<string, MePrivacyModeResponse | null> = {
  allowCloud: me("allow_cloud"),
  fetchFailed: null,
  locked: me("local_only", { locked: true }),
  localOnly: me("local_only"),
  ownRestriction: me("allow_cloud", { restriction: "local_only" }),
  ownRestrictionUnderLock: me("allow_cloud", { locked: true, restriction: "local_only" }),
};

const ADMIN = "/admin/privacy-mode";
const ASK = "Ask your household admin if you need them.";
const link = (to: string | null, label: string | null) => ({ linkTo: to, linkLabel: label });
const copy = (text: string, to: string | null = null, label: string | null = null): PrivacyHintCopy => ({
  text,
  ...link(to, label),
});

interface Case {
  payload: keyof typeof PAYLOADS;
  isAdmin: boolean;
  surface: PrivacyHintSurface;
  adminTier?: boolean;
  row: number | null;
  expected: PrivacyHintCopy | null;
}

const CASES: Case[] = [
  // Row 1
  { payload: "allowCloud", isAdmin: true, surface: "picker", row: 1, expected: null },
  { payload: "allowCloud", isAdmin: false, surface: "key-save", row: 1, expected: null },
  // Row 2
  {
    payload: "fetchFailed", isAdmin: true, surface: "picker", row: 2,
    expected: copy("Cloud models are off because privacy mode is local-only.", ADMIN, "Privacy mode settings"),
  },
  {
    payload: "fetchFailed", isAdmin: false, surface: "picker", row: 2,
    expected: copy(`Cloud models are off because privacy mode is local-only. ${ASK}`),
  },
  { payload: "fetchFailed", isAdmin: true, surface: "key-save", row: 2, expected: null },
  { payload: "fetchFailed", isAdmin: false, surface: "key-save", row: 2, expected: null },
  // Row 3
  {
    payload: "locked", isAdmin: true, surface: "picker", row: 3,
    expected: copy("Cloud models are off: household privacy mode is locked to local-only.", ADMIN, "Unlock it in Privacy mode"),
  },
  {
    payload: "locked", isAdmin: false, surface: "picker", row: 3,
    expected: copy(`Cloud models are off: your household's privacy mode is locked to local-only. ${ASK}`),
  },
  {
    payload: "locked", isAdmin: true, surface: "key-save", row: 3,
    expected: copy("Cloud models stay off: household privacy mode is locked to local-only.", ADMIN, "Unlock it in Privacy mode"),
  },
  {
    payload: "locked", isAdmin: false, surface: "key-save", row: 3,
    expected: copy(`Cloud models stay off: your household's privacy mode is locked to local-only. ${ASK}`),
  },
  // Row 4
  {
    payload: "localOnly", isAdmin: true, surface: "picker", row: 4,
    expected: copy("Cloud models are off because household privacy mode is local-only.", ADMIN, "Allow cloud models in Privacy mode"),
  },
  {
    payload: "localOnly", isAdmin: false, surface: "picker", row: 4,
    expected: copy(`Cloud models are off because your household's privacy mode is local-only. ${ASK}`),
  },
  {
    payload: "localOnly", isAdmin: true, surface: "key-save", row: 4,
    expected: copy("Cloud models stay off while household privacy mode is local-only.", ADMIN, "Allow cloud models in Privacy mode"),
  },
  {
    payload: "localOnly", isAdmin: false, surface: "key-save", row: 4,
    expected: copy(`Cloud models stay off while your household's privacy mode is local-only. ${ASK}`),
  },
  {
    payload: "localOnly", isAdmin: true, surface: "key-save", adminTier: true, row: 4,
    expected: copy("Cloud models stay off while household privacy mode is local-only.", ADMIN, "Allow cloud models in Privacy mode"),
  },
  // Row 5
  {
    payload: "ownRestriction", isAdmin: false, surface: "picker", row: 5,
    expected: copy(
      "Cloud models are off because you limited your account to local models.",
      "/me/privacy-mode",
      "Change it in your privacy settings",
    ),
  },
  {
    payload: "ownRestriction", isAdmin: true, surface: "key-save", row: 5,
    expected: copy(
      "Cloud models stay off for you because you limited your account to local models.",
      "/me/privacy-mode",
      "Change it in your privacy settings",
    ),
  },
  { payload: "ownRestriction", isAdmin: true, surface: "key-save", adminTier: true, row: 5, expected: null },
  {
    payload: "ownRestriction", isAdmin: true, surface: "picker", adminTier: true, row: 5,
    expected: copy(
      "Cloud models are off because you limited your account to local models.",
      "/me/privacy-mode",
      "Change it in your privacy settings",
    ),
  },
  {
    // The Me page hides its toggle under a lock, so the link would dead-end.
    payload: "ownRestrictionUnderLock", isAdmin: false, surface: "picker", row: 5,
    expected: copy("Cloud models are off because you limited your account to local models."),
  },
];

describe("privacyBlockCopy", () => {
  it.each(CASES.map((c, i) => [`#${i} row ${c.row} ${c.payload} admin=${c.isAdmin} ${c.surface}${c.adminTier ? " adminTier" : ""}`, c] as const))(
    "%s",
    (_name, c) => {
      const out = privacyBlockCopy({
        me: PAYLOADS[c.payload],
        isAdmin: c.isAdmin,
        surface: c.surface,
        adminTier: c.adminTier,
      });
      expect(out).toEqual(c.expected);
    },
  );

  it("never hands a member the admin link, on any payload or surface", () => {
    for (const payload of Object.values(PAYLOADS)) {
      for (const surface of ["picker", "key-save"] as const) {
        const out = privacyBlockCopy({ me: payload, isAdmin: false, surface });
        expect(out?.linkTo ?? null).not.toBe(ADMIN);
      }
    }
  });
});

describe("catalogHasPrivacyBlock", () => {
  const m = (over: Partial<Omit<ModelDescriptor, "disabled_reason">> & { disabled_reason?: unknown }): ModelDescriptor =>
    ({ id: "x", label: "X", is_local: false, enabled: false, provider: "p", ...over }) as ModelDescriptor;

  it("is false for an empty catalog", () => {
    expect(catalogHasPrivacyBlock([])).toBe(false);
  });
  it("is false when no model reads privacy_mode (other reasons, omitted, unknown)", () => {
    expect(
      catalogHasPrivacyBlock([
        m({ disabled_reason: "no_credentials" }),
        m({ disabled_reason: "optional_disabled" }),
        m({}),
        m({ disabled_reason: null }),
        m({ disabled_reason: "something_new" }),
      ]),
    ).toBe(false);
  });
  it("is true when one model reads privacy_mode", () => {
    expect(catalogHasPrivacyBlock([m({ enabled: true }), m({ disabled_reason: "privacy_mode" })])).toBe(true);
  });
});
