// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin household-sharing governance (LUM-584): review every item shared with
// the household and retract one on a member's behalf (retract-only, audited).
// Distinct from the owner-only unshare each member has on their own items —
// this is admin governance, server-gated by require_admin.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  adminUnshare,
  fetchAdminSharedItems,
  type AdminSharedItem,
} from "../../api/adminSharedItems";
import { ApiError } from "../../api/client";
import { useAuth, useUser } from "../../auth/AuthProvider";

const RESOURCE_LABELS: Record<AdminSharedItem["resource_type"], string> = {
  notes: "note",
  audio_memos: "audio memo",
  sessions: "session",
  files: "document",
  entities: "entity",
  signals: "signal",
};

// The API returns structured errors as `detail: { error, message }`. The
// client's safeReadDetail can't unwrap a non-string detail, so ApiError.detail
// arrives as a JSON blob — pull out the human-authored `message` when present
// (governance failures like "Couldn't fully unshare — please retry" matter to
// the admin), falling back to the raw text.
function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    try {
      const parsed = JSON.parse(e.detail) as {
        detail?: { message?: unknown };
        message?: unknown;
      };
      const msg = parsed?.detail?.message ?? parsed?.message;
      if (typeof msg === "string" && msg.length > 0) return msg;
    } catch {
      /* not JSON — use the raw detail below */
    }
    return e.detail;
  }
  if (e instanceof Error) return e.message;
  return "Request failed";
}

function rowKey(item: AdminSharedItem): string {
  return `${item.resource_type}:${item.resource_id}`;
}

function ownerLabel(item: AdminSharedItem): string {
  return item.source_owner_id ?? "another member";
}

export function AdminSharedItemsView(): JSX.Element | null {
  const { client } = useAuth();
  const user = useUser();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";

  const listQ = useQuery({
    queryKey: ["admin", "shared-items"],
    queryFn: () => fetchAdminSharedItems(client),
    enabled: isAdmin,
  });

  const unshareM = useMutation({
    mutationFn: (item: AdminSharedItem) =>
      adminUnshare(client, item.resource_type, item.resource_id),
    onSuccess: () => {
      setConfirming(null);
      void qc.invalidateQueries({ queryKey: ["admin", "shared-items"] });
    },
    onError: (e) => setErr(errMsg(e)),
  });

  // Defense-in-depth: the /admin route already gates the whole subtree, but the
  // retract affordance must never render for a non-admin (mirrors the server
  // gate). Placed after the hooks so hook order stays stable.
  if (user && !isAdmin) return null;

  const items = listQ.data?.items ?? [];

  return (
    <section className="lumogis-admin-dense-section" data-testid="admin-shared-items">
      <h2>Household shared items</h2>
      <p style={{ opacity: 0.8 }}>
        Everything members have shared with the household. As an admin you can
        retract a share on a member&apos;s behalf — this removes the household
        copy only; the owner keeps their private original and can re-share it.
      </p>

      {err && (
        <p role="alert" style={{ color: "#c62828" }}>
          {err}
        </p>
      )}

      {listQ.isPending && <p role="status">Loading…</p>}
      {listQ.isError && <p role="alert">Failed to load shared items.</p>}
      {listQ.isSuccess && items.length === 0 && (
        <p role="status">Nothing is shared with the household yet.</p>
      )}

      {listQ.isSuccess && items.length > 0 && (
        <table className="lumogis-admin-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Shared by</th>
              <th>Item</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const key = rowKey(item);
              const type = RESOURCE_LABELS[item.resource_type];
              return (
                <tr key={key}>
                  <td>{type}</td>
                  <td>{ownerLabel(item)}</td>
                  <td>
                    <span className="lumogis-long-text">{item.label ?? item.resource_id}</span>
                  </td>
                  <td>
                    {confirming === key ? (
                      <span data-testid={`confirm-${key}`}>
                        <span style={{ marginRight: "0.5rem" }}>
                          Unshare {ownerLabel(item)}&apos;s {type} from the household?
                        </span>
                        <button
                          type="button"
                          disabled={unshareM.isPending}
                          data-testid={`confirm-force-unshare-${key}`}
                          onClick={() => {
                            setErr(null);
                            unshareM.mutate(item);
                          }}
                        >
                          Confirm
                        </button>{" "}
                        <button
                          type="button"
                          disabled={unshareM.isPending}
                          onClick={() => setConfirming(null)}
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        data-testid={`force-unshare-${key}`}
                        onClick={() => {
                          setErr(null);
                          setConfirming(key);
                        }}
                      >
                        Force unshare
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
