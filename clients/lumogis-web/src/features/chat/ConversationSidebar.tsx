// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Server-backed conversation history sidebar (LUM-162).

import { Share2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useCallback, useEffect, useId, useMemo, useState } from "react";

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

  const headingId = useId();
  // LUM-993: same data and actions, new look — one line per chat with its
  // summary under it. Share and Delete sit over the row's right end and show on
  // hover or keyboard focus (always on touch, and while Share is open): they
  // take no width, so titles use it all, and they stay in the accessibility tree.
  const action =
    "inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-background hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 pointer-coarse:size-[44px]";
  return (
    <section className="flex flex-col gap-1" data-testid="conversation-sidebar" aria-labelledby={headingId}>
      <h2 id={headingId} className="m-0 px-2 text-xs font-medium text-muted-foreground">
        History
      </h2>
      {loading && <p className="m-0 px-2 text-xs text-muted-foreground">Loading…</p>}
      {error !== null && (
        <p role="alert" className="m-0 px-2 text-xs text-destructive">
          {error}
        </p>
      )}
      {!loading && items.length === 0 && unresolvedPending.length === 0 && (
        <p className="m-0 px-2 text-xs text-muted-foreground">Closed chats show up here once they are summarised.</p>
      )}
      {unresolvedPending.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0" role="list" aria-label="Conversations being summarised">
          {unresolvedPending.map((p) => (
            <li
              key={p.conversationId}
              className="flex flex-col rounded-md px-2 py-1.5"
              data-conversation-id={p.conversationId}
              data-testid="pending-summary"
            >
              <span className="truncate text-sm">{p.title}</span>
              <span role="status" className="text-xs text-muted-foreground">
                Summarising…
              </span>
            </li>
          ))}
        </ul>
      )}
      {partialToast !== null && (
        <p role="status" className="m-0 px-2 text-xs text-destructive">
          <Button
            type="button"
            variant="link"
            size="xs"
            className="h-auto p-0 text-xs text-destructive underline"
            onClick={() => setPartialToast(null)}
          >
            {partialToast}
          </Button>
        </p>
      )}
      <ul className="m-0 flex list-none flex-col gap-2 p-0" role="list">
        {CONVERSATION_GROUP_ORDER.map((group) => {
          const rows = grouped[group];
          if (rows.length === 0) return null;
          return (
            <li key={group} className="flex flex-col gap-0.5">
              <span className="px-2 pt-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                {CONVERSATION_GROUP_LABELS[group]}
              </span>
              <ul className="m-0 flex list-none flex-col gap-0.5 p-0" role="list">
                {rows.map((c) => (
                  <li key={c.conversation_id} className="group flex flex-col" data-conversation-id={c.conversation_id}>
                    <div className="relative flex items-center gap-0.5 rounded-md hover:bg-muted pointer-coarse:pr-1">
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-auto justify-start whitespace-normal font-normal hover:bg-transparent flex min-h-9 min-w-0 flex-1 flex-col items-start rounded-md px-2 py-1.5 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 pointer-coarse:min-h-[44px]"
                        onClick={() => void onSelectContinue(c.conversation_id)}
                      >
                        <span className="w-full truncate text-sm">
                          {c.title}
                          {c.share_status === "shared" ? (
                            <span
                              className="text-xs text-muted-foreground"
                              data-testid={`conversation-list-shared-badge-${c.conversation_id}`}
                            >
                              {" "}
                              · Shared
                            </span>
                          ) : null}
                        </span>
                        <span className="w-full truncate text-xs text-muted-foreground">{c.summary}</span>
                      </Button>
                      <div
                        className={cn(
                          "absolute top-1/2 right-1 flex -translate-y-1/2 gap-0.5 rounded-md bg-muted opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:static pointer-coarse:translate-y-0 pointer-coarse:bg-transparent pointer-coarse:opacity-100",
                          shareOpenId === c.conversation_id && "opacity-100",
                        )}
                      >
                        {c.is_owner !== false ? (
                          <Button
                            type="button"
                            variant="ghost"
                            className={action}
                            aria-label={`Share ${c.title}`}
                            aria-expanded={shareOpenId === c.conversation_id}
                            data-testid={`share-conversation-${c.conversation_id}`}
                            onClick={() => setShareOpenId((cur) => (cur === c.conversation_id ? null : c.conversation_id))}
                          >
                            <Share2 className="size-3.5" aria-hidden="true" />
                          </Button>
                        ) : null}
                        {/* Only the owner can delete: the server answers 404 to anyone else
                            (conversations.delete_conversation is scoped to the caller). */}
                        {c.is_owner !== false ? (
                          <Button
                            type="button"
                            variant="ghost"
                            className={cn(action, "hover:text-destructive")}
                            aria-label={`Delete ${c.title}`}
                            onClick={() => void onDelete(c.conversation_id)}
                          >
                            <Trash2 className="size-3.5" aria-hidden="true" />
                          </Button>
                        ) : null}
                      </div>
                    </div>
                    {shareOpenId === c.conversation_id ? (
                      <div className="px-2 pt-1 pb-2">
                        <ShareConversation client={client} conversationId={c.conversation_id} onChanged={() => void load()} />
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
