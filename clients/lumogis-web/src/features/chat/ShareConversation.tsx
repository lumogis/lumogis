// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Share a conversation with the household (LUM-582 Rung 1). The owner shares an
// editable AI summary that becomes searchable household knowledge — a
// point-in-time snapshot. Editing the summary here never touches the owner's
// private canonical summary (server-side). A non-owner sees a read-only
// indicator; a not-yet-summarized (web-only) conversation can't be shared yet.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { ApiClient } from "../../api/client";
import { ApiError } from "../../api/client";
import {
  getConversation,
  publishConversation,
  unpublishConversation,
} from "../../api/conversations";

export interface ShareConversationProps {
  client: ApiClient;
  conversationId: string;
  onChanged?: () => void;
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

export function ShareConversation({
  client,
  conversationId,
  onChanged,
}: ShareConversationProps): JSX.Element | null {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const detailQ = useQuery({
    queryKey: ["conversation", conversationId, "detail"],
    queryFn: () => getConversation(client, conversationId),
  });

  const detail = detailQ.data;
  const shared = detail?.share_status === "shared";

  // Prefill the editor from the current household-facing summary when already
  // shared (so a re-share preserves the prior edit), else the AI summary.
  useEffect(() => {
    if (!editing && detail) {
      setDraft((shared ? detail.shared_summary : detail.summary) ?? detail.summary ?? "");
    }
  }, [detail, shared, editing]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
    void qc.invalidateQueries({ queryKey: ["conversations"] });
    onChanged?.();
  };

  const publishM = useMutation({
    mutationFn: () => publishConversation(client, conversationId, { shared_summary: draft }),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
    onError: (e) => setErr(errMsg(e)),
  });

  const unshareM = useMutation({
    mutationFn: () => unpublishConversation(client, conversationId),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
    onError: (e) => setErr(errMsg(e)),
  });

  if (detailQ.isPending) return <p role="status">Loading…</p>;
  if (!detail) return null;

  // Non-owner: read-only indicator, no mutation affordance.
  if (detail.is_owner === false) {
    return (
      <div className="lumogis-share-conversation" data-testid="conversation-share-indicator">
        <span className="lumogis-share-toggle__readonly">Shared with your household</span>
      </div>
    );
  }

  // Owner but not shareable yet (web-only / un-summarized conversation).
  if (detail.can_share === false) {
    return (
      <div className="lumogis-share-conversation" data-testid="conversation-share">
        <span className="lumogis-share-toggle__readonly">
          You can share this once it&apos;s been summarized.
        </span>
      </div>
    );
  }

  const pending = publishM.isPending || unshareM.isPending;

  return (
    <div className="lumogis-share-conversation" data-testid="conversation-share">
      {err && (
        <p role="alert" style={{ color: "#c62828" }}>
          {err}
        </p>
      )}

      {!editing ? (
        shared ? (
          <div>
            <span className="lumogis-share-toggle__readonly" data-testid="conversation-shared-badge">
              Shared with your household
            </span>{" "}
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                setErr(null);
                setEditing(true);
              }}
            >
              Edit summary
            </button>{" "}
            <button
              type="button"
              disabled={pending}
              data-testid="conversation-unshare"
              onClick={() => {
                setErr(null);
                unshareM.mutate();
              }}
            >
              Unshare
            </button>
          </div>
        ) : (
          <button
            type="button"
            disabled={pending}
            data-testid="conversation-share-start"
            onClick={() => {
              setErr(null);
              setEditing(true);
            }}
          >
            Share with your household
          </button>
        )
      ) : (
        <div>
          <label>
            Summary shared with your household (edit if the AI got it wrong)
            <textarea
              data-testid="conversation-share-summary"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={4}
            />
          </label>
          <p className="lumogis-share-toggle__hint" data-testid="conversation-snapshot-notice">
            You&apos;re sharing this as it is now — new messages won&apos;t be shared
            automatically.
          </p>
          <button
            type="button"
            disabled={pending}
            data-testid="conversation-share-confirm"
            onClick={() => {
              setErr(null);
              publishM.mutate();
            }}
          >
            {shared ? "Update shared summary" : "Share"}
          </button>{" "}
          <button type="button" disabled={pending} onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
