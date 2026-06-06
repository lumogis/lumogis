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

export interface ConversationSidebarProps {
  client: ApiClient;
  onContinue(seedMessages: import("../../api/chat").ChatMessageDTO[]): void;
  refreshToken?: number;
}

export function ConversationSidebar({
  client,
  onContinue,
  refreshToken = 0,
}: ConversationSidebarProps): JSX.Element {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [partialToast, setPartialToast] = useState<string | null>(null);

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
      {!loading && items.length === 0 && (
        <p className="lumogis-chat__history-empty">
          Ended chats appear here after summarization completes.
        </p>
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
                      <span className="lumogis-chat__history-title">{c.title}</span>
                      <span className="lumogis-chat__history-summary">{c.summary}</span>
                    </button>
                    <button
                      type="button"
                      className="lumogis-chat__history-delete"
                      aria-label={`Delete ${c.title}`}
                      onClick={() => void onDelete(c.conversation_id)}
                    >
                      ×
                    </button>
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
