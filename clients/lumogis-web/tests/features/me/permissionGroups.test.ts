// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-849 — the words the autonomy page uses, and the union it renders.
import { describe, expect, it } from "vitest";

import {
  PERMISSION_GROUPS,
  connectorsToRender,
  groupFor,
} from "../../../src/features/me/permissionGroups";

describe("groupFor", () => {
  it("maps smtp to the irreversible question", () => {
    const g = groupFor("smtp");
    expect(g.title).toBe("Send email as you");
    expect(g.irreversible).toBe(true);
    expect(g.doLabel).toBe("Just send");
  });

  it("maps imap to the reversible question", () => {
    const g = groupFor("imap");
    expect(g.title).toBe("Tidy your mailbox");
    expect(g.irreversible).toBe(false);
    expect(g.doLabel).toBe("Just do it");
  });

  it("renders the irreversible question first", () => {
    // Order is rendering order. The question a person cannot take back should
    // be read while they are still paying attention.
    const [first] = Object.keys(PERMISSION_GROUPS);
    expect(PERMISSION_GROUPS[first!]!.irreversible).toBe(true);
  });

  it("falls back honestly for a connector it has no words for", () => {
    const g = groupFor("capability.summarise");
    // The raw id must survive into the title: a live permission that the page
    // cannot describe must still be findable, not dropped.
    expect(g.title).toContain("capability.summarise");
    expect(g.detail).toMatch(/act on your behalf/i);
    expect(g.consequence).toMatch(/don't have a plain-language description/i);
  });

  it("gives every group the same scope line", () => {
    // Load-bearing. Permissions are UNIQUE (user_id, connector) — there is no
    // per-mailbox grain — and every other email control in the product IS
    // per-mailbox, so without this line the wrong inference is the natural one.
    const groups = [...Object.values(PERMISSION_GROUPS), groupFor("capability.x")];
    for (const g of groups) {
      expect(g.scopeNote).toBe("Applies to all your mailboxes.");
    }
  });

  it("never returns a raw mode string as a label", () => {
    for (const g of [...Object.values(PERMISSION_GROUPS), groupFor("unknown")]) {
      expect(g.askLabel).not.toMatch(/^(ASK|DO)$/);
      expect(g.doLabel).not.toMatch(/^(ASK|DO)$/);
    }
  });
});

describe("connectorsToRender", () => {
  it("keeps a known group even when the server sent no row for it", () => {
    // The degrade shape: registry down, so the route returns explicit rows only
    // and `imap` (never configured by this user) is absent. The question must
    // still render — dropping it would omit a live permission from the page.
    expect(connectorsToRender(["smtp"])).toContain("imap");
  });

  it("keeps a row the client has no group for", () => {
    expect(connectorsToRender(["capability.foo"])).toContain("capability.foo");
  });

  it("renders the union when both are present", () => {
    // The assertion that fails under EITHER single-input iteration strategy.
    const out = connectorsToRender(["smtp", "capability.foo"]);
    expect(out).toEqual(expect.arrayContaining(["smtp", "imap", "capability.foo"]));
  });

  it("does not duplicate a connector the server repeated", () => {
    const out = connectorsToRender(["capability.foo", "capability.foo", "smtp"]);
    expect(out.filter((c) => c === "capability.foo")).toHaveLength(1);
    expect(out.filter((c) => c === "smtp")).toHaveLength(1);
  });
});
