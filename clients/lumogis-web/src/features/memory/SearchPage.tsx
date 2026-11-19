// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Memory search surface — parent plan §"Phase 1 Pass 1.3 item 9".
//
// SearchPage provides a debounced query box that fans out to:
//   1. GET /api/v1/memory/search  — semantic / full-text document hits
//   2. GET /api/v1/kg/search      — entity name-search (KG)
//
// Selecting a hit from either list opens the EntityCard panel (item 10).
// The two results lists are shown side-by-side on desktop and stacked on mobile.
//
// Co-located helpers follow the same pattern as ChatPage.tsx; the
// react-refresh rule is suppressed once for the whole file.
/* eslint-disable react-refresh/only-export-components */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

import { Link } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import type { ApiClient } from "../../api/client";
import {
  memorySearch,
  kgSearch,
  isEntityShared,
  type EntityCard,
  type MemoryScope,
  type MemorySearchHit,
} from "../../api/search";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { PageHeader } from "../../components/page/PageHeader";
import { EntityCardPanel } from "./EntityCard";

// ── Debounce hook ─────────────────────────────────────────────────────────

export function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

// ── Search hook ───────────────────────────────────────────────────────────

export interface SearchResults {
  memoryHits: MemorySearchHit[];
  entityHits: EntityCard[];
  degraded: boolean;
  loading: boolean;
  error: string | null;
  refetchEntities: () => Promise<void>;
}

export function useSearch(client: ApiClient, query: string): SearchResults {
  const [memoryHits, setMemoryHits] = useState<MemorySearchHit[]>([]);
  const [entityHits, setEntityHits] = useState<EntityCard[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const refetchEntities = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) {
      setEntityHits([]);
      return;
    }
    try {
      const res = await kgSearch(client, trimmed, 10);
      setEntityHits(res.entities);
    } catch {
      setEntityHits([]);
    }
  }, [client, query]);

  useEffect(() => {
    if (!query.trim()) {
      setMemoryHits([]);
      setEntityHits([]);
      setDegraded(false);
      setError(null);
      setLoading(false);
      return;
    }

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setLoading(true);
    setError(null);

    Promise.allSettled([
      memorySearch(client, query, 10, ctrl.signal),
      kgSearch(client, query, 10, ctrl.signal),
    ]).then(([memRes, kgRes]) => {
      if (ctrl.signal.aborted) return;

      if (memRes.status === "fulfilled") {
        setMemoryHits(memRes.value.hits);
        setDegraded(memRes.value.degraded);
      } else {
        setMemoryHits([]);
        setError("Memory search unavailable.");
      }

      if (kgRes.status === "fulfilled") {
        setEntityHits(kgRes.value.entities);
      } else {
        setEntityHits([]);
      }

      setLoading(false);
    });

    return () => {
      ctrl.abort();
    };
  }, [client, query]);

  return { memoryHits, entityHits, degraded, loading, error, refetchEntities };
}

// ── Component ─────────────────────────────────────────────────────────────

export function SearchPage(): JSX.Element {
  const { client } = useAuth();
  const [rawQuery, setRawQuery] = useState("");
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [sharedOnly, setSharedOnly] = useState(false);

  const query = useDebounced(rawQuery, 300);
  const { memoryHits, entityHits, degraded, loading, error, refetchEntities } =
    useSearch(client, query);

  // LUM-581 — client-side "Shared with household" filter over the
  // household-union entity results (no server-side ?scope=shared for v1).
  const visibleEntities = sharedOnly
    ? entityHits.filter((e) => isEntityShared(e.share_status))
    : entityHits;

  const handleInput = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setRawQuery(e.target.value);
    setSelectedEntityId(null);
  }, []);

  const handleEntitySelect = useCallback((entityId: string) => {
    setSelectedEntityId((prev) => (prev === entityId ? null : entityId));
  }, []);

  return (
    <section aria-label="Memory search">
      <PageHeader
        title="Search"
        description={
          <Link to="/entities" className="text-accent-text underline underline-offset-2">
            Browse what Lumogis knows →
          </Link>
        }
      />
      <div className="relative mb-4 max-w-xl">
        <Input
          type="text"
          placeholder="Search memories and entities…"
          value={rawQuery}
          onChange={handleInput}
          aria-label="Search query"
          aria-busy={loading}
          autoComplete="off"
          spellCheck={false}
        />
        {loading && (
          <span
            className="absolute right-3 top-1/2 size-4 -translate-y-1/2 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground"
            aria-hidden="true"
          />
        )}
      </div>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {degraded && !error && (
        <Alert className="mb-4" role="status">
          <AlertDescription>
            Memory search is degraded — results may be incomplete.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        {/* Memory hits */}
        <section aria-label="Memory results" className="min-w-0 space-y-3">
          <h2 className="m-0 text-base font-semibold">Memories</h2>
          {!query.trim() && (
            <p className="m-0 text-sm text-muted-foreground">Type to search your memories.</p>
          )}
          {query.trim() && memoryHits.length === 0 && !loading && (
            <p className="m-0 text-sm text-muted-foreground">No memory hits.</p>
          )}
          <ul className="m-0 list-none space-y-2 p-0" role="list">
            {memoryHits.map((hit) => (
              <li
                key={hit.id}
                className="space-y-1 rounded-lg border border-border bg-card p-3 text-card-foreground"
              >
                <div className="text-sm font-medium">{hit.title ?? hit.id}</div>
                {hit.snippet && (
                  <p className="m-0 text-sm text-muted-foreground">{hit.snippet}</p>
                )}
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <ScopeBadge scope={hit.scope} />
                  {hit.source && <span>{hit.source}</span>}
                  <span>{(hit.score * 100).toFixed(0)}%</span>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* Entity hits */}
        <section aria-label="Entity results" className="min-w-0 space-y-3">
          <h2 className="m-0 text-base font-semibold">Entities</h2>
          <div className="flex items-center gap-2">
            <Checkbox
              id="search-shared-only"
              checked={sharedOnly}
              onCheckedChange={(checked) => setSharedOnly(checked === true)}
              aria-label="Shared with household"
            />
            <Label htmlFor="search-shared-only" className="font-normal">
              Shared with household
            </Label>
          </div>
          {!query.trim() && (
            <p className="m-0 text-sm text-muted-foreground">Type to search entities.</p>
          )}
          {query.trim() && visibleEntities.length === 0 && !loading && (
            <p className="m-0 text-sm text-muted-foreground">
              {sharedOnly && entityHits.length > 0
                ? "No shared entities."
                : "No entity hits."}
            </p>
          )}
          <ul className="m-0 list-none space-y-2 p-0" role="list">
            {visibleEntities.map((entity) => (
              <li key={entity.entity_id}>
                <Button
                  type="button"
                  variant={selectedEntityId === entity.entity_id ? "secondary" : "outline"}
                  className="h-auto w-full justify-start gap-2 whitespace-normal py-2 text-left"
                  onClick={() => handleEntitySelect(entity.entity_id)}
                  aria-expanded={selectedEntityId === entity.entity_id}
                >
                  <span className="font-medium">{entity.name}</span>
                  {entity.type && (
                    <span className="text-xs font-normal text-muted-foreground">{entity.type}</span>
                  )}
                  {/* Personal is the default and stays unmarked; only shared
                      entries get a badge (state shown once, LUM-993). */}
                  {isEntityShared(entity.share_status) && (
                    <Badge variant="secondary" aria-label="Shared with household">
                      Shared
                    </Badge>
                  )}
                </Button>

                {selectedEntityId === entity.entity_id && (
                  <EntityCardPanel
                    entityId={entity.entity_id}
                    client={client}
                    initialCard={entity}
                    onShareChanged={() => void refetchEntities()}
                    // LUM-966: a Hide or Show again changes what the search returns.
                    onEdited={() => void refetchEntities()}
                  />
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </section>
  );
}

// ── Scope badge ───────────────────────────────────────────────────────────

const SCOPE_LABELS: Record<MemoryScope, string> = {
  personal: "Personal",
  shared: "Shared",
  system: "Built in",
};

function ScopeBadge({ scope }: { scope: MemoryScope }): JSX.Element {
  return <Badge variant="outline">{SCOPE_LABELS[scope] ?? scope}</Badge>;
}
