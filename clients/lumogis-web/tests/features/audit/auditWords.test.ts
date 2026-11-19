// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { describe, expect, it } from "vitest";

import { auditSentence } from "../../../src/features/audit/_shared/auditWords";

describe("auditSentence", () => {
  it("maps a session code to a plain sentence", () => {
    expect(auditSentence({ action_name: "__session__.minted", event_type: "auth.session.minted" })).toBe("Signed in");
  });

  it("maps by event type when the action has no sentence", () => {
    expect(auditSentence({ action_name: "", event_type: "privacy.external_call.denied" })).toMatch(/blocked/);
  });

  it("tidies an unmapped action name instead of hiding it", () => {
    expect(auditSentence({ action_name: "lumogis.tests.ok_action", event_type: "action.executed" })).toBe(
      "Lumogis tests ok action",
    );
    expect(auditSentence({ action_name: "__weird__.thing", event_type: "x.y" })).toBe("Weird thing");
  });

  it("covers permission changes and routines", () => {
    expect(auditSentence({ action_name: "__permissions_change__.imap", event_type: "auth.permissions.changed" })).toBe(
      "What Lumogis may do was changed",
    );
    expect(auditSentence({ action_name: "routine:daily", event_type: "action.routine.executed" })).toBe("A routine ran");
  });
});
