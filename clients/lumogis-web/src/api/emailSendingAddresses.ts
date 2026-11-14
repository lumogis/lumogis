// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The address each of the caller's mailboxes sends FROM (LUM-831).
//
// Resolved on demand from the credential, never stored a second time, so what
// the From row shows cannot drift from what the send uses.
//
// Entries are keyed on credential rows while the web keys on `sources` rows, so
// a mailbox with no entry is normal — it degrades to its name. This map is NOT
// authoritative for "your mailboxes"; `sources` is.

import type { ApiClient } from "./client";

export interface SendingAddressOut {
  source_id: string;
  address: string;
}

export interface SendingAddressesResponse {
  items: SendingAddressOut[];
}

/** `source_id` → sending address, for mailboxes whose address resolved. */
export async function fetchSendingAddresses(api: ApiClient): Promise<Record<string, string>> {
  const res = await api.getJson<SendingAddressesResponse>("/api/v1/email/sending-addresses");
  const out: Record<string, string> = {};
  for (const item of res?.items ?? []) {
    if (item.source_id && item.address) out[item.source_id] = item.address;
  }
  return out;
}
