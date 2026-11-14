// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-769 — the credentials read the mailbox card's send state comes from.

import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import {
  hasSmtpCredential,
  listMyCredentials,
  type ConnectorCredentialSummary,
} from "../../src/api/connectorCredentials";

const row = (over: Partial<ConnectorCredentialSummary> = {}): ConnectorCredentialSummary => ({
  connector: "smtp",
  instance: "src-1",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  ...over,
});

describe("hasSmtpCredential", () => {
  it("matches on connector AND instance", () => {
    expect(hasSmtpCredential([row()], "src-1")).toBe(true);
  });

  it("does not count an smtp row belonging to a different mailbox", () => {
    // The case that makes both halves of the match load-bearing: a household
    // member with two mailboxes has one smtp row per enabled mailbox. Matching
    // on `connector` alone would report sending as ON for every mailbox the
    // moment it was on for any one of them.
    expect(hasSmtpCredential([row({ instance: "src-2" })], "src-1")).toBe(false);
  });

  it("does not count a non-smtp row for the same mailbox", () => {
    // Every mailbox has an `imap` row; only some have `smtp`.
    expect(hasSmtpCredential([row({ connector: "imap" })], "src-1")).toBe(false);
  });

  it("is false for an empty list", () => {
    expect(hasSmtpCredential([], "src-1")).toBe(false);
  });
});

describe("listMyCredentials", () => {
  it("requests the full /api/v1 path", async () => {
    // This assertion exists because every fetch mock in this suite is
    // SUBSTRING-matched, so a wrong path would pass all of them. The signals
    // feature's own idiom (`/me/email-sources/…`) is un-prefixed and correct for
    // its root-mounted router; copying it here would produce
    // `/me/connector-credentials`, which 404s — and nothing else would notice.
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ApiClient({ tokens: new AccessTokenStore(), fetchImpl });

    await listMyCredentials(client);

    const url = String(fetchImpl.mock.calls[0][0]);
    expect(url).toBe("/api/v1/me/connector-credentials");
  });
});
