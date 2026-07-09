// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Server-backed conversation history sidebar (LUM-162).

import { useCallback, useEffect, useMemo, useState } from "react";

import type { ApiClient } from "../../api/client";
import {
  continueConversation,
  deleteConversation,
  listConversations,
  type ConversationSummary,
} from "../../api/conversations";
import {
  CONVERSATION_GROUP_LABELS,
  CONVERSATION_GROUP_ORDER,
  groupConversationByEndedAt,
  type ConversationGroup,
} from "./conversationGroups";
import { ShareConversation } from "./ShareConversation";

/**
 * A conversation whose `session/end` was enqueued but whose `sessions` row has
 * not yet been written by the batch summarisation job (LUM-417). The sidebar
 * shows a "Summarising…" placeholder for these until the real row appears.
 */
export interface PendingSummary {
  conversationId: string;
  title: string;
}

/** Poll cadence + cap while waiting for a summary row to materialise. */
const PENDING_POLL_INTERVAL_MS = 3000;
const PENDING_POLL_MAX_ATTEMPTS = 20; // ~60s, then give up rather than spin forever.

export interface ConversationSidebarProps {
  client: ApiClient;
  onContinue(seedMessages: import("../../api/chat").ChatMessageDTO[]): void;
  refreshToken?: number;
  /** Sessions just ended locally, awaiting their summary row. */
  pendingSummaries?: PendingSummary[];
  /** Fired with ids that have resolved (row arrived) or expired (poll cap). */
  onPendingResolved?(conversationIds: string[]): void;
}

export function ConversationSidebar({
  client,
  onContinue,
  refreshToken = 0,
  pendingSummaries = [],
  onPendingResolved,
}: ConversationSidebarProps): JSX.Element {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [partialToast, setPartialToast] = useState<string | null>(null);
  const [shareOpenId, setShareOpenId] = useState<string | null>(null); // LUM-582

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listConversations(client);
      setItems(res.conversations);
    } catch (err) {
      setError(err instanceof Error ? err.message : "load_failed");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  // Pending summaries still absent from the fetched list — the only ones we
  // render as "Summarising…" placeholders.
  const unresolvedPending = useMemo(
    () =>
      pendingSummaries.filter(
        (p) => !items.some((it) => it.conversation_id === p.conversationId),
      ),
    [pendingSummaries, items],
  );

  // When a pending row's real summary arrives, tell the parent to drop it.
  useEffect(() => {
    if (onPendingResolved === undefined) return;
    const resolved = pendingSummaries
      .filter((p) => items.some((it) => it.conversation_id === p.conversationId))
      .map((p) => p.conversationId);
    if (resolved.length > 0) onPendingResolved(resolved);
  }, [items, pendingSummaries, onPendingResolved]);

  // While any summary is still pending, poll the list so the placeholder
  // auto-resolves without a manual refresh. Bounded so a failed batch job
  // cannot leave the sidebar polling forever.
  const pendingKey = useMemo(
    () =>
      unresolvedPending
        .map((p) => p.conversationId)
        .sort()
        .join(","),
    [unresolvedPending],
  );
  useEffect(() => {
    if (pendingKey.length === 0) return;
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (attempts >= PENDING_POLL_MAX_ATTEMPTS) {
        clearInterval(timer);
        onPendingResolved?.(pendingKey.split(","));
        return;
      }
      void load();
    }, PENDING_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [pendingKey, load, onPendingResolved]);

  const grouped = useMemo(() => {
    const buckets: Record<ConversationGroup, ConversationSummary[]> = {
      today: [],
      yesterday: [],
      last7: [],
      older: [],
    };
    for (const c of items) {
      buckets[groupConversationByEndedAt(c.ended_at)].push(c);
    }
    return buckets;
  }, [items]);

  const onDelete = useCallback(
    async (conversationId: string) => {
      if (!globalThis.confirm("Delete this conversation permanently?")) return;
      try {
        const res = await deleteConversation(client, conversationId);
        setItems((prev) => prev.filter((c) => c.conversation_id !== conversationId));
        if (res.partial) {
          setPartialToast(
            "Deletion incomplete — some copies may remain. Tap to retry.",
          );
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "delete_failed");
      }
    },
    [client],
  );

  const onSelectContinue = useCallback(
    async (conversationId: string) => {
      try {
        const res = await continueConversation(client, conversationId);
        onContinue(res.seed_messages);
      } catch (err) {
        setError(err instanceof Error ? err.message : "continue_failed");
      }
    },
    [client, onContinue],
  );

  return (
    <div className="lumogis-chat__history" data-testid="conversation-sidebar">
      <h3 className="lumogis-chat__history-heading">History</h3>
      {loading && <p className="lumogis-chat__history-status">Loading…</p>}
      {error !== null && (
        <p role="alert" className="lumogis-chat__history-error">
          {error}
        </p>
      )}
      {!loading && items.length === 0 && unresolvedPending.length === 0 && (
        <p className="lumogis-chat__history-empty">
          Ended chats appear here after summarization completes.
        </p>
      )}
      {unresolvedPending.length > 0 && (
        <ul
          className="lumogis-chat__history-pending"
          role="list"
          aria-label="Conversations being summarised"
        >
          {unresolvedPending.map((p) => (
            <li
              key={p.conversationId}
              className="lumogis-chat__history-item lumogis-chat__history-item--pending"
              data-conversation-id={p.conversationId}
              data-testid="pending-summary"
            >
              <span className="lumogis-chat__history-title">{p.title}</span>
              <span
                role="status"
                className="lumogis-chat__history-summary lumogis-chat__history-pending-label"
              >
                Summarising…
              </span>
            </li>
          ))}
        </ul>
      )}
      {partialToast !== null && (
        <p role="status" className="lumogis-chat__history-partial">
          <button type="button" onClick={() => setPartialToast(null)}>
            {partialToast}
          </button>
        </p>
      )}
      <ul className="lumogis-chat__history-list" role="list">
        {CONVERSATION_GROUP_ORDER.map((group) => {
          const rows = grouped[group];
          if (rows.length === 0) return null;
          return (
            <li key={group} className="lumogis-chat__history-group">
              <span className="lumogis-chat__history-group-label">
                {CONVERSATION_GROUP_LABELS[group]}
              </span>
              <ul role="list">
                {rows.map((c) => (
                  <li
                    key={c.conversation_id}
                    className="lumogis-chat__history-item"
                    data-conversation-id={c.conversation_id}
                  >
                    <button
                      type="button"
                      className="lumogis-chat__history-select"
                      onClick={() => void onSelectContinue(c.conversation_id)}
                    >
                      <span className="lumogis-chat__history-title">
                        {c.title}
                        {c.share_status === "shared" ? (
                          <span
                            className="lumogis-chat__history-shared-badge"
                            data-testid={`conversation-list-shared-badge-${c.conversation_id}`}
                          >
                            {" "}
                            · Shared
                          </span>
                        ) : null}
                      </span>
                      <span className="lumogis-chat__history-summary">{c.summary}</span>
                    </button>
                    {c.is_owner !== false ? (
                      <button
                        type="button"
                        className="lumogis-chat__history-share"
                        aria-label={`Share ${c.title}`}
                        data-testid={`share-conversation-${c.conversation_id}`}
                        onClick={() =>
                          setShareOpenId((cur) =>
                            cur === c.conversation_id ? null : c.conversation_id,
                          )
                        }
                      >
                        Share
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="lumogis-chat__history-delete"
                      aria-label={`Delete ${c.title}`}
                      onClick={() => void onDelete(c.conversation_id)}
                    >
                      ×
                    </button>
                    {shareOpenId === c.conversation_id ? (
                      <ShareConversation
                        client={client}
                        conversationId={c.conversation_id}
                        onChanged={() => void load()}
                      />
                    ) : null}
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
