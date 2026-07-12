// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Settings → My shared items (LUM-583): everything the member has shared with
// the household, in one place, with per-row unshare. Read-only aggregate +
// reuse of the owner-only per-type unpublish route — this page never shares.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  listMySharedItems,
  unshareMyItem,
  type SharedItem,
} from "../../api/sharedItems";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";

const RESOURCE_LABELS: Record<SharedItem["resource_type"], string> = {
  notes: "note",
  audio_memos: "audio memo",
  sessions: "session",
  files: "document",
  entities: "entity",
  signals: "signal",
};

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
      /* not JSON — fall back to the raw detail */
    }
    return e.detail;
  }
  if (e instanceof Error) return e.message;
  return "Request failed";
}

function rowKey(item: SharedItem): string {
  return `${item.resource_type}:${item.resource_id}`;
}

function sharedOn(item: SharedItem): string | null {
  if (!item.shared_at) return null;
  const d = new Date(item.shared_at);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString();
}

export function MeSharedItemsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["me", "shared-items"],
    queryFn: () => listMySharedItems(client),
  });

  const unshareM = useMutation({
    mutationFn: (item: SharedItem) =>
      unshareMyItem(client, item.resource_type, item.resource_id),
    onSuccess: () => {
      setConfirming(null);
      void qc.invalidateQueries({ queryKey: ["me", "shared-items"] });
    },
    onError: (e) => setErr(errMsg(e)),
  });

  const items = listQ.data?.items ?? [];

  return (
    <section className="lumogis-admin-dense-section" data-testid="me-shared-items">
      <h2>My shared items</h2>
      <p style={{ opacity: 0.8 }}>
        Everything you&apos;ve shared with your household. Unsharing removes the
        household copy; your private original stays with you and you can share
        it again anytime.
      </p>

      {err && (
        <p role="alert" style={{ color: "#c62828" }}>
          {err}
        </p>
      )}

      {listQ.isPending && <p role="status">Loading…</p>}
      {listQ.isError && <p role="alert">Failed to load your shared items.</p>}
      {listQ.isSuccess && items.length === 0 && (
        <p role="status">You haven&apos;t shared anything with your household yet.</p>
      )}

      {listQ.isSuccess && items.length > 0 && (
        <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table lumogis-responsive-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Item</th>
              <th>Shared</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const key = rowKey(item);
              const type = RESOURCE_LABELS[item.resource_type];
              const on = sharedOn(item);
              return (
                <tr key={key}>
                  <td data-label="Type">{type}</td>
                  <td data-label="Item">
                    <span className="lumogis-long-text">
                      {item.label ?? `Untitled ${type}`}
                    </span>
                  </td>
                  <td data-label="Shared">{on ?? "—"}</td>
                  <td data-label="">
                    {confirming === key ? (
                      <span data-testid={`confirm-${key}`}>
                        <span style={{ marginRight: "0.5rem" }}>
                          Unshare this {type} from your household?
                        </span>
                        <button
                          type="button"
                          disabled={unshareM.isPending}
                          data-testid={`confirm-unshare-${key}`}
                          onClick={() => {
                            setErr(null);
                            unshareM.mutate(item);
                          }}
                        >
                          Unshare
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
                        data-testid={`unshare-${key}`}
                        onClick={() => {
                          setErr(null);
                          setConfirming(key);
                        }}
                      >
                        Unshare
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </section>
  );
}
