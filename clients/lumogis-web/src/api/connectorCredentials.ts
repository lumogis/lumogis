// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-769 — read the caller's connector credentials so the mailbox card can say
// whether sending is on.
//
// Send state is NOT on `GET /sources`: enablement *is* the existence of an
// `smtp` credential row, which this endpoint already publishes. That is why this
// chunk adds no column and no `sources` field — a persisted `smtp_enabled` would
// be a second source of truth for a question the credential row already answers.

import type { ApiClient } from "./client";

/**
 * A narrowing of the server's `ConnectorCredentialPublic`.
 *
 * Deliberately no longer restates the server's field inventory: the previous
 * version of this comment enumerated "eleven fields" and was left stale by the
 * very change that added three more. Declaring only what we use is the point —
 * a server-side addition cannot break us, and it should not oblige an edit
 * here either.
 * Metadata only — never ciphertext, never a payload; the server enforces that
 * with `extra="forbid"` on the model.
 */
export interface ConnectorCredentialSummary {
  connector: string;
  instance: string;
  created_at: string;
  updated_at: string;
  /** LUM-834 chunk B — user-set on/off. Absent on pre-chunk-B servers. */
  enabled?: boolean;
  /**
   * Payload key NAMES the user has set — never a value.
   *
   * **THREE states, and `?? []` is forbidden on this field.**
   *
   * `null` means UNKNOWN: the row predates migration 079, so which fields are
   * set genuinely cannot be known without decrypting it — which is the thing
   * this whole design exists to avoid. It does NOT mean "no fields set": a
   * pre-migration credential is usually a working one.
   *
   * `?? []` / `|| []` is this codebase's dominant idiom for an optional array
   * (60+ occurrences under `src/`), and applying it here collapses unknown
   * into nothing-set at the render layer — rendering a working connector as
   * unconfigured, two layers below where the nullable column was fought for.
   * Destructure it explicitly and branch on `null`/`undefined`.
   */
  configured_fields?: string[] | null;
  /**
   * Which declared fields hold secrets. From the REGISTRY, not the row.
   * Never null on a chunk-B server; optional here only for older ones.
   */
  secret_fields?: string[];
}

/**
 * List the caller's connector credentials.
 *
 * The path is `/api/v1/me/connector-credentials` — the FULL prefix, matching the
 * router mount in `routes/connector_credentials.py`.
 *
 * `emailSources.ts` genuinely mixes two path idioms and BOTH are correct:
 * `ApiClient.resolveUrl` prepends only `baseUrl` (default empty) and never adds
 * `/api/v1`, so the un-prefixed `/me/email-sources/…` that `setEmailSmtp` uses is
 * right for the signals router, which mounts at the root, while this one needs
 * the full prefix because its own router mounts there. Copying the nearer
 * neighbour gives the wrong path — and every fetch mock in the test suite is
 * substring-matched, so a wrong path would pass all of them. There is a test
 * that pins this exact URL for that reason.
 */
export function listMyCredentials(
  client: ApiClient,
): Promise<{ items: ConnectorCredentialSummary[] }> {
  return client.getJson("/api/v1/me/connector-credentials");
}

/**
 * True iff an SMTP credential row exists for this mailbox.
 *
 * Both halves of the match matter. `connector` alone is not enough: a household
 * member with two mailboxes has an `smtp` row per enabled mailbox, and matching
 * on the connector only would report sending as on for every one of them the
 * moment it was on for any.
 */
export function hasSmtpCredential(
  items: ConnectorCredentialSummary[],
  sourceId: string,
): boolean {
  return items.some((c) => c.connector === "smtp" && c.instance === sourceId);
}

/**
 * The server's `ConnectorTestResponse` (LUM-834).
 *
 * `ok` is **tri-state**: `true` verified, `false` tried and failed, `null` NOT
 * ATTEMPTED. Treating it as a boolean paints a red ✗ on a connector that has no
 * probe and is perfectly healthy — and chunk B's four-state chip is built on
 * these three states, so collapsing it breaks that card before it is written.
 *
 * Key UI on `code`, never on `detail`. `detail` is server-owned copy and is
 * free to be reworded; `code` is a frozen `Literal` on the wire.
 */
export interface ConnectorTestResult {
  ok: boolean | null;
  code:
    | "verified"
    | "auth_failed"
    | "unreachable"
    | "not_configured"
    | "credential_unavailable"
    | "invalid_config"
    | "unsupported"
    | "tested_elsewhere"
    // LUM-834 chunk B — the credential is stored and switched off. `ok` is
    // `null` here: nothing was tried, so nothing failed.
    | "disabled";
  detail: string;
}

/**
 * Test whether the caller's stored credential for `connector` works.
 *
 * Full `/api/v1` prefix for the same reason `listMyCredentials` uses one — this
 * router mounts there, and copying the nearer neighbour in `emailSources.ts`
 * gives the wrong path against substring-matched fetch mocks that would pass
 * anyway.
 *
 * A failed test is a `200` with `ok: false`, not a thrown `ApiError`. Only a
 * `429`, a `404`, or a refusal rejects.
 */
export function testConnector(client: ApiClient, connector: string): Promise<ConnectorTestResult> {
  return client.postJson<Record<string, never>, ConnectorTestResult>(
    `/api/v1/me/connector-credentials/${encodeURIComponent(connector)}/test`,
    {},
  );
}
