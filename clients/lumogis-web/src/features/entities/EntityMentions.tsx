// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "Mentioned in" section on the entry page (LUM-161). The server returns only
// items the viewer can open (GET /api/v1/kg/entities/{id}/mentions); this
// component renders them. Documents link to /documents/{ref_id}. Chats and
// AI-tool additions are labels only: Web has no open-a-chat-by-id route yet.
/* eslint-disable react-refresh/only-export-components */

import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import type { ApiClient } from "../../api/client";
import {
  getEntityMentions,
  type EntityMention,
  type EntityMentionCounts,
  type EntityMentionsResponse,
} from "../../api/search";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Button } from "../../components/ui/button";
import { formatDateTime } from "../../util/formatTimestamp";

const PAGE_SIZE = 50;

export function formatMentionDate(iso: string): string {
  return formatDateTime(iso, { withTime: false });
}

/** "3 documents, 2 chats, 1 AI-tool addition" — only non-zero kinds. */
export function mentionCountsSentence(counts: EntityMentionCounts): string {
  const parts: string[] = [];
  if (counts.document > 0) {
    parts.push(`${counts.document} ${counts.document === 1 ? "document" : "documents"}`);
  }
  if (counts.chat > 0) {
    parts.push(`${counts.chat} ${counts.chat === 1 ? "chat" : "chats"}`);
  }
  if (counts.ai_tool > 0) {
    parts.push(
      `${counts.ai_tool} ${counts.ai_tool === 1 ? "AI-tool addition" : "AI-tool additions"}`,
    );
  }
  return parts.join(", ");
}

export interface EntityMentionsProps {
  entityId: string;
  client: ApiClient;
  /** False for another member's shared entry (adds the "only shared items" note). */
  isOwner?: boolean;
}

export function EntityMentions({
  entityId,
  client,
  isOwner = true,
}: EntityMentionsProps): JSX.Element {
  const q = useInfiniteQuery({
    queryKey: ["kg", "entities", "mentions", entityId],
    queryFn: ({ pageParam }: { pageParam: number }) =>
      getEntityMentions(client, entityId, { limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last: EntityMentionsResponse, all: EntityMentionsResponse[]) => {
      const loaded = all.reduce((n, p) => n + p.mentions.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
  });

  if (q.isError) {
    return (
      <PageSection title="Mentioned in">
        <ErrorState
          title="Couldn't load mentions."
          message="Please try again in a moment."
          onRetry={() => void q.refetch()}
          doctorHint={false}
        />
      </PageSection>
    );
  }

  if (q.isPending) {
    return (
      <PageSection title="Mentioned in">
        <LoadingPlaceholder label="Loading…">
          <SkeletonText lines={3} />
        </LoadingPlaceholder>
      </PageSection>
    );
  }

  const pages = q.data.pages;
  const counts = pages[0]!.counts;
  const mentions: EntityMention[] = pages.flatMap((p) => p.mentions);
  const sentence = mentionCountsSentence(counts);

  return (
    <PageSection title="Mentioned in">
      {sentence ? (
        <p className="m-0 text-sm">Mentioned in {sentence}</p>
      ) : (
        <p className="m-0 text-sm text-muted-foreground">No mentions you can open.</p>
      )}
      {!isOwner && (
        <p className="m-0 mt-1 text-xs text-muted-foreground">
          Only items shared with the household are listed.
        </p>
      )}
      {mentions.length > 0 && (
        <ul role="list" className="m-0 mt-3 flex list-none flex-col divide-y divide-border p-0">
          {mentions.map((m) => (
            <li key={`${m.kind}:${m.ref_id}`} className="py-1.5 text-sm" data-kind={m.kind}>
              {m.kind === "document" && (
                <>
                  <Link
                    to={`/documents/${encodeURIComponent(m.ref_id)}`}
                    className="text-accent-text underline underline-offset-2"
                  >
                    {m.title}
                  </Link>{" "}
                  <span className="text-muted-foreground">· {formatMentionDate(m.mentioned_at)}</span>
                </>
              )}
              {m.kind === "chat" && (
                <>
                  <span className="text-muted-foreground">
                    Chat · {formatMentionDate(m.mentioned_at)}
                  </span>{" "}
                  <span>{m.title}</span>
                </>
              )}
              {m.kind === "ai_tool" && (
                <span className="text-muted-foreground">
                  Added by an AI tool · {formatMentionDate(m.mentioned_at)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {q.hasNextPage && (
        <Button
          type="button"
          variant="outline"
          className="mt-3"
          onClick={() => void q.fetchNextPage()}
          disabled={q.isFetchingNextPage}
        >
          {q.isFetchingNextPage ? "Loading…" : "Show more"}
        </Button>
      )}
    </PageSection>
  );
}
