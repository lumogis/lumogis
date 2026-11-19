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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { LoadingPlaceholder, SkeletonText } from "@/components/page/LoadingState";

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

  if (detailQ.isPending)
    return (
      <LoadingPlaceholder label="Loading…">
        <SkeletonText lines={1} />
      </LoadingPlaceholder>
    );
  if (!detail) return null;

  // Non-owner: read-only indicator, no mutation affordance.
  if (detail.is_owner === false) {
    return (
      <div className="flex flex-col gap-2" data-testid="conversation-share-indicator">
        <Badge variant="secondary">Shared with your household</Badge>
      </div>
    );
  }

  // Owner but not shareable yet (web-only / un-summarized conversation).
  if (detail.can_share === false) {
    return (
      <div className="flex flex-col gap-2" data-testid="conversation-share">
        <span className="text-sm text-muted-foreground">
          You can share this once it&apos;s been summarized.
        </span>
      </div>
    );
  }

  const pending = publishM.isPending || unshareM.isPending;

  return (
    <div className="flex flex-col gap-3" data-testid="conversation-share">
      {err && (
        <Alert variant="destructive">
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      )}

      {!editing ? (
        shared ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" data-testid="conversation-shared-badge">
              Shared with your household
            </Badge>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pending}
              onClick={() => {
                setErr(null);
                setEditing(true);
              }}
            >
              Edit summary
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pending}
              data-testid="conversation-unshare"
              onClick={() => {
                setErr(null);
                unshareM.mutate();
              }}
            >
              Unshare
            </Button>
          </div>
        ) : (
          <div>
            <Button
              type="button"
              size="sm"
              disabled={pending}
              data-testid="conversation-share-start"
              onClick={() => {
                setErr(null);
                setEditing(true);
              }}
            >
              Share with your household
            </Button>
          </div>
        )
      ) : (
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Summary shared with your household (edit if the AI got it wrong)
            <Textarea
              data-testid="conversation-share-summary"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={4}
            />
          </label>
          <p className="m-0 text-xs text-muted-foreground" data-testid="conversation-snapshot-notice">
            You&apos;re sharing this as it is now — new messages won&apos;t be shared
            automatically.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={pending}
              data-testid="conversation-share-confirm"
              onClick={() => {
                setErr(null);
                publishM.mutate();
              }}
            >
              {shared ? "Update shared summary" : "Share"}
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={pending} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
