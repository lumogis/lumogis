// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin → System mail (LUM-65). The single write path for the `system_smtp`
// credential: the generic connector-credential surfaces refuse that connector
// with 422 `use_system_mail_admin`, so this module is the only client that can
// configure the household's transactional mailer.

import type { ApiClient } from "./client";

/**
 * Server's `SystemMailPublic`. The password is never present in any form —
 * `password_set` is all the form needs to render "leave blank to keep".
 *
 * The three `*_set` booleans exist because enablement is a conjunction; an
 * operator seeing `email_reset_enabled: false` needs to know which conjunct is
 * missing. The *public* status endpoint deliberately withholds that.
 */
export interface SystemMailPublic {
  configured: boolean;
  host: string | null;
  port: number | null;
  username: string | null;
  from_addr: string | null;
  password_set: boolean;
  last_test_ok_at: string | null;
  email_reset_enabled: boolean;
  public_origin_set: boolean;
  altcha_hmac_set: boolean;
}

export interface SystemMailPutBody {
  host: string;
  port: number;
  username: string;
  /** Omit to keep the stored password. `""`/`null` is a 422, not a clear. */
  password?: string;
  from_addr: string;
}

export interface SystemMailTestResponse {
  ok: boolean;
  last_test_ok_at: string;
}

export function getSystemMail(client: ApiClient): Promise<SystemMailPublic> {
  return client.getJson<SystemMailPublic>("/api/v1/admin/system-mail");
}

export function putSystemMail(
  client: ApiClient,
  body: SystemMailPutBody,
): Promise<SystemMailPublic> {
  return client.putJson<SystemMailPutBody, SystemMailPublic>(
    "/api/v1/admin/system-mail",
    body,
  );
}

export function deleteSystemMail(client: ApiClient): Promise<void> {
  return client.delete("/api/v1/admin/system-mail");
}

/**
 * Send a probe to `to` (default: the calling admin's own account email) and,
 * only if the relay accepts it, stamp `last_test_ok_at` — which is what turns
 * the public forgot-password form on.
 */
export function testSystemMail(
  client: ApiClient,
  to?: string,
): Promise<SystemMailTestResponse> {
  return client.postJson<{ to?: string }, SystemMailTestResponse>(
    "/api/v1/admin/system-mail/test",
    to ? { to } : {},
  );
}
