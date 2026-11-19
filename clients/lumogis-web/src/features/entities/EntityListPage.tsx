// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "What Lumogis knows" (LUM-161) — the member's entries by kind, with counts.
// Reads GET /api/v1/kg/entities (Postgres, identical in every GRAPH_MODE).
// Reached from the Search page; /entities* highlights the Search nav item.

import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation } from "react-router-dom";

import {
  listEntities,
  type EntityDeleteResponse,
  type EntityKind,
  type EntityListItem,
  type EntityListResponse,
  type EntityListShared,
  type EntityListSort,
} from "../../api/search";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageHeader } from "../../components/page/PageHeader";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { formatDateTime } from "../../util/formatTimestamp";

const PAGE_SIZE = 50;

type KindTab = "all" | EntityKind;
type SharedFilter = "all" | EntityListShared;

const TABS: ReadonlyArray<{ key: KindTab; label: string }> = [
  { key: "all", label: "All" },
  { key: "person", label: "People" },
  { key: "org", label: "Organisations" },
  { key: "project", label: "Projects" },
  { key: "concept", label: "Concepts" },
  { key: "other", label: "Other" },
];

const SHARED_LABELS: Record<SharedFilter, string> = {
  all: "All entries",
  only: "Shared with household",
  exclude: "Only mine",
};

const SORT_LABELS: Record<EntityListSort, string> = {
  mentions: "Most mentioned",
  recent: "Recently mentioned",
  name: "A–Z",
};

function formatDate(iso: string): string {
  return formatDateTime(iso, { withTime: false });
}

/**
 * What a delete changed, in the member's words (LUM-966): the names now on her list of
 * names Lumogis won't learn, and any it kept learning (another entry of hers uses the
 * name, or a kind correction already sends it elsewhere).
 */
export function DeletedNotice({ deleted }: { deleted: EntityDeleteResponse }): JSX.Element {
  const names = deleted.blocked_names.map((n) => `“${n}”`);
  const joined =
    names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
  return (
    <Alert className="mb-4 max-w-2xl" role="status" data-testid="entity-deleted-notice">
      <AlertDescription className="flex flex-col gap-1">
      <p className="m-0">
        {names.length > 0
          ? `Deleted. ${joined} ${names.length === 1 ? "is" : "are"} on your list of names Lumogis won't learn.`
          : "Deleted."}{" "}
        <Link to="/settings/wont-learn" className="text-accent-text underline underline-offset-2">
          See the list
        </Link>
      </p>
      {deleted.not_blocked.map((n) => (
        <p key={n.name} className="m-0">
          {n.reason === "used_by_other_entry"
            ? `“${n.name}” is still used by another of your entries, so Lumogis keeps learning it for that entry.`
            : `“${n.name}” already has a kind correction, so Lumogis keeps applying that.`}
        </p>
      ))}
      </AlertDescription>
    </Alert>
  );
}

export function EntityListPage(): JSX.Element {
  const { client } = useAuth();
  const location = useLocation();
  const deleted = (location.state as { deleted?: EntityDeleteResponse } | null)?.deleted;
  const [kind, setKind] = useState<KindTab>("all");
  const [shared, setShared] = useState<SharedFilter>("all");
  const [sort, setSort] = useState<EntityListSort>("mentions");

  const q = useInfiniteQuery({
    queryKey: ["kg", "entities", { kind, shared, sort }],
    queryFn: ({ pageParam }: { pageParam: number }) =>
      listEntities(client, {
        kind: kind === "all" ? undefined : kind,
        shared: shared === "all" ? undefined : shared,
        sort,
        limit: PAGE_SIZE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (last: EntityListResponse, all: EntityListResponse[]) => {
      const loaded = all.reduce((n, p) => n + p.entities.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    // Keep the tabs, controls and rows on screen while another kind / filter /
    // sort loads, so focus and layout do not jump (LUM-161 verify).
    placeholderData: keepPreviousData,
  });

  const pages = q.data?.pages;
  const counts = pages?.[0]?.counts;
  const allCount = counts
    ? counts.person + counts.org + counts.project + counts.concept + counts.other
    : 0;
  const items: EntityListItem[] = pages ? pages.flatMap((p) => p.entities) : [];
  const filtered = kind !== "all" || shared !== "all";
  // A failed first load replaces the page; a failed "Show more" keeps the rows
  // already loaded and says so next to the button.
  const loadFailed = q.isError && !pages;

  return (
    <section data-testid="entity-list-page">
      <p className="mb-2">
        <Link to="/search" className="text-accent-text underline underline-offset-2">
          ← Search
        </Link>
      </p>
      <PageHeader
        title="What Lumogis knows"
        description="The people, organisations, projects and ideas Lumogis has found in your documents and chats."
      />

      {deleted && <DeletedNotice deleted={deleted} />}

      {loadFailed && (
        <ErrorState
          title="Couldn't load what Lumogis knows."
          message="Please try again in a moment."
          onRetry={() => void q.refetch()}
          doctorHint={false}
        />
      )}

      {q.isPending && (
        <LoadingPlaceholder label="Loading…">
          <SkeletonText lines={5} />
        </LoadingPlaceholder>
      )}

      {!loadFailed && counts && allCount === 0 && !filtered && (
        <EmptyState
          title="Lumogis hasn't learned anything yet"
          helperText="Add documents, chat, or capture a note. People, organisations, projects and ideas it finds will show up here."
          actions={[{ label: "Add documents", href: "/documents", primary: true }]}
        />
      )}

      {!loadFailed && counts && (allCount > 0 || filtered) && (
        <>
          <Tabs value={kind} onValueChange={(v) => setKind(v as KindTab)} className="mb-4">
            <TabsList aria-label="Kind" className="group-data-horizontal/tabs:h-auto pointer-coarse:group-data-horizontal/tabs:h-auto flex-wrap justify-start">
              {TABS.map((t) => {
                const n = t.key === "all" ? allCount : counts[t.key];
                return (
                  <TabsTrigger key={t.key} value={t.key} className="flex-none px-3 py-1">
                    {t.label} ({n})
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </Tabs>

          <div className="mb-4 flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="entity-filter">Show</Label>
              <Select value={shared} onValueChange={(v) => setShared(String(v ?? "all") as SharedFilter)}>
                <SelectTrigger id="entity-filter" aria-label="Filter" className="w-56">
                  <SelectValue>{(v: string | null) => SHARED_LABELS[(v ?? "all") as SharedFilter]}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(SHARED_LABELS) as SharedFilter[]).map((value) => (
                    <SelectItem key={value} value={value}>
                      {SHARED_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="entity-sort">Sort</Label>
              <Select value={sort} onValueChange={(v) => setSort(String(v ?? "mentions") as EntityListSort)}>
                <SelectTrigger id="entity-sort" aria-label="Sort" className="w-56">
                  <SelectValue>{(v: string | null) => SORT_LABELS[(v ?? "mentions") as EntityListSort]}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(SORT_LABELS) as EntityListSort[]).map((value) => (
                    <SelectItem key={value} value={value}>
                      {SORT_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {items.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing matches this filter.</p>
          ) : (
            <ul role="list" className="m-0 flex max-w-2xl list-none flex-col divide-y divide-border p-0">
              {items.map((e) => (
                <EntityRow key={e.entity_id} entity={e} />
              ))}
            </ul>
          )}

          {q.isFetchNextPageError && (
            <Alert variant="destructive" className="mt-3 max-w-2xl">
              <AlertDescription>Couldn't load more. Try again.</AlertDescription>
            </Alert>
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
        </>
      )}
    </section>
  );
}

function EntityRow({ entity: e }: { entity: EntityListItem }): JSX.Element {
  const shownAliases = e.aliases.slice(0, 3);
  const more = e.alias_count - shownAliases.length;
  return (
    <li className="flex flex-col gap-0.5 py-2" data-testid="entity-list-row">
      <div className="flex items-center gap-2">
        <Link
          to={`/entities/${encodeURIComponent(e.entity_id)}`}
          className="text-accent-text font-medium underline underline-offset-2"
        >
          {e.name}
        </Link>
        {e.share_status === "shared" && (
          <Badge variant="secondary" aria-label="Shared with household">
            Shared
          </Badge>
        )}
      </div>
      {shownAliases.length > 0 && (
        <p className="m-0 text-sm text-muted-foreground">
          also: {shownAliases.join(", ")}
          {more > 0 ? ` +${more} more` : ""}
        </p>
      )}
      {(e.mention_count !== null || e.last_mentioned_at !== null) && (
        <p className="m-0 text-xs text-muted-foreground">
          {e.mention_count !== null &&
            `Mentioned in ${e.mention_count} ${e.mention_count === 1 ? "item" : "items"}`}
          {e.mention_count !== null && e.last_mentioned_at !== null && " · "}
          {e.last_mentioned_at !== null && `last mentioned ${formatDate(e.last_mentioned_at)}`}
        </p>
      )}
      {!e.is_owner && (
        <p className="m-0 text-xs text-muted-foreground">Shared by a household member</p>
      )}
    </li>
  );
}
