// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-837 chunk B — the consent state machine, tested at the reducer.
//
// The send rule is one sentence — "every body carries exactly the flags true in
// `acked`" — and these tests exist because three rounds of plan review each
// stated it as a path-specific table that was wrong on the paths it omitted.
// Every path below is asserted separately, with its precondition named.
import { describe, expect, it } from "vitest";

import { ApiError } from "../../../src/api/client";
import { STARVATION_CODE } from "../../../src/features/signals/externalConsumerWarning";
import { OVERLAP_CODE } from "../../../src/features/email/paperlessOverlapWarning";
import {
  consentFlags,
  consentReducer,
  initialConsentState,
  type ConsentState,
} from "../../../src/features/me/sourceConsent";

/** The wire shape both parsers expect: `detail` is an object, and the shared
 *  client stringifies the whole body into `ApiError.detail`. */
function refusal(code: string, extra: Record<string, unknown> = {}): ApiError {
  return new ApiError(
    422,
    JSON.stringify({
      detail: { code, message: "m", alternative: "a", recovery: "r", ...extra },
    }),
  );
}

const starvation = () => refusal(STARVATION_CODE);
const overlap = () =>
  refusal(OVERLAP_CODE, { mailboxes: [{ source_name: "Personal", rule_count: 3 }] });

/** Drive the reducer through a list of actions from a given start. */
function run(actions: Parameters<typeof consentReducer>[1][], from = initialConsentState) {
  return actions.reduce(consentReducer, from);
}

// A settled credential read, so the first-resolution exemption is not in play.
const settled: ConsentState = consentReducer(initialConsentState, {
  type: "credentialRead",
  at: "2026-08-18T09:00:00Z",
});

describe("the send rule", () => {
  it("sends nothing on the first attempt", () => {
    expect(consentFlags(settled)).toEqual({});
  });

  it("sends exactly the flags that are true, for every reachable combination", () => {
    expect(consentFlags(settled)).toEqual({});
    expect(
      consentFlags(run([{ type: "refused", error: starvation() }, { type: "acknowledged" }], settled)),
    ).toEqual({ acknowledge_external_consumer: true });
    expect(
      consentFlags(run([{ type: "refused", error: overlap() }, { type: "acknowledged" }], settled)),
    ).toEqual({ acknowledge_paperless_overlap: true });
  });
});

describe("the three paths", () => {
  it("overlap-only: the re-post carries overlap and NOT starvation", () => {
    // The commonest paperless household — no folder-sorting mailbox, so the
    // starvation arm never fires. Sending `acknowledge_external_consumer` here
    // would be acknowledging a card the user was never shown.
    const s = run([{ type: "refused", error: overlap() }, { type: "acknowledged" }], settled);
    expect(consentFlags(s)).toEqual({ acknowledge_paperless_overlap: true });
    expect(consentFlags(s).acknowledge_external_consumer).toBeUndefined();
  });

  it("starvation-only: the re-post carries starvation and NOT overlap", () => {
    const s = run([{ type: "refused", error: starvation() }, { type: "acknowledged" }], settled);
    expect(consentFlags(s)).toEqual({ acknowledge_external_consumer: true });
  });

  it("two-arm: the THIRD request carries both — dropping either is an infinite loop", () => {
    // The server checks the starvation arm first and unconditionally on every
    // request, so a re-post without `acknowledge_external_consumer` re-triggers
    // the arm the user already answered. Forever.
    const afterFirst = run(
      [{ type: "refused", error: starvation() }, { type: "acknowledged" }],
      settled,
    );
    expect(consentFlags(afterFirst)).toEqual({ acknowledge_external_consumer: true });

    const afterSecond = run(
      [{ type: "submitted" }, { type: "refused", error: overlap() }, { type: "acknowledged" }],
      afterFirst,
    );
    expect(consentFlags(afterSecond)).toEqual({
      acknowledge_external_consumer: true,
      acknowledge_paperless_overlap: true,
    });
  });
});

describe("what is shown", () => {
  it("shows at most one card, and it is the arm the server refused with", () => {
    const s = consentReducer(settled, { type: "refused", error: overlap() });
    expect(s.shown?.arm).toBe("overlap");
    // The discriminant narrows the union — `mailboxes` exists only on the
    // overlap arm, which is the point of tagging the card by arm at all.
    if (s.shown?.arm !== "overlap") throw new Error("expected the overlap card");
    expect(s.shown.warning.mailboxes).toEqual([{ source_name: "Personal", rule_count: 3 }]);
  });

  it("clears the card on submit, so a stale refusal never sits under a request in flight", () => {
    const s = run([{ type: "refused", error: starvation() }, { type: "submitted" }], settled);
    expect(s.shown).toBeNull();
  });

  it("acknowledging with no card shown changes nothing", () => {
    expect(consentReducer(settled, { type: "acknowledged" })).toEqual(settled);
  });
});

describe("failures leave consents alone", () => {
  it("a 422 with an unrecognised code neither shows a card nor drops a flag", () => {
    const acked = run([{ type: "refused", error: starvation() }, { type: "acknowledged" }], settled);
    const after = consentReducer(acked, { type: "refused", error: refusal("something_else") });
    expect(after).toEqual(acked);
    expect(consentFlags(after)).toEqual({ acknowledge_external_consumer: true });
  });

  it("a 500 leaves the accumulated acknowledgements intact so pressing again retries", () => {
    const acked = run([{ type: "refused", error: starvation() }, { type: "acknowledged" }], settled);
    const after = consentReducer(acked, {
      type: "refused",
      error: new ApiError(500, "Could not save source: boom"),
    });
    expect(consentFlags(after)).toEqual({ acknowledge_external_consumer: true });
  });
});

describe("what resets the acknowledgements", () => {
  it("changing the URL or the type clears both", () => {
    const acked = run([{ type: "refused", error: starvation() }, { type: "acknowledged" }], settled);
    expect(consentFlags(consentReducer(acked, { type: "inputsChanged" }))).toEqual({});
  });

  it("success clears both, so the next add starts clean", () => {
    const acked = run([{ type: "refused", error: overlap() }, { type: "acknowledged" }], settled);
    expect(consentFlags(consentReducer(acked, { type: "succeeded" }))).toEqual({});
  });

  it("a credential APPEARING clears them", () => {
    const acked = run([{ type: "refused", error: overlap() }, { type: "acknowledged" }], settled);
    const withNone = consentReducer(acked, { type: "credentialRead", at: null });
    expect(consentFlags(withNone)).toEqual({});
  });

  it("a credential being REPLACED clears them — the case presence alone cannot see", () => {
    // The overlap answer is computed from the credential's paperless instance.
    // A user who concludes "wrong server", fixes the credential and re-posts
    // would otherwise carry an answer given about a different instance.
    const acked = run([{ type: "refused", error: overlap() }, { type: "acknowledged" }], settled);
    expect(acked.credentialAt).toBe("2026-08-18T09:00:00Z");
    const replaced = consentReducer(acked, {
      type: "credentialRead",
      at: "2026-08-18T10:30:00Z",
    });
    expect(consentFlags(replaced)).toEqual({});
    expect(replaced.shown).toBeNull();
  });

  it("an unchanged credential read does NOT clear them — refetch on focus is not an edit", () => {
    const acked = run([{ type: "refused", error: overlap() }, { type: "acknowledged" }], settled);
    const same = consentReducer(acked, { type: "credentialRead", at: "2026-08-18T09:00:00Z" });
    expect(consentFlags(same)).toEqual({ acknowledge_paperless_overlap: true });
  });

  it("the FIRST credential resolution does not clear them", () => {
    // `credentialAt` starts at "unknown" and the query settling is not a change
    // the user made. Clearing here would drop a consent given while the read
    // was still in flight.
    const acked = run(
      [{ type: "refused", error: overlap() }, { type: "acknowledged" }],
      initialConsentState,
    );
    const resolved = consentReducer(acked, { type: "credentialRead", at: null });
    expect(consentFlags(resolved)).toEqual({ acknowledge_paperless_overlap: true });
  });

  it("editing name, category or poll interval does NOT clear them", () => {
    // The deliberate narrowing of the shipped precedent. Neither guard reads
    // those fields, so a blanket clear-on-any-edit would re-open the loop
    // through the UI while protecting nothing. There is no action for such an
    // edit *by design* — this test pins the absence.
    const acked = run([{ type: "refused", error: overlap() }, { type: "acknowledged" }], settled);
    const actions = ["submitted", "refused", "acknowledged", "succeeded", "inputsChanged", "credentialRead"];
    expect(actions).not.toContain("fieldEdited");
    expect(consentFlags(acked)).toEqual({ acknowledge_paperless_overlap: true });
  });
});
