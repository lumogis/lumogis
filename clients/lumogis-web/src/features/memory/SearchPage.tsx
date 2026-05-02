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
// The two results lists are shown side-by-side on desktop (≥720 px) and
// stacked on mobile via the existing CSS container-query breakpoints.
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

import { useAuth } from "../../auth/AuthProvider";
import type { ApiClient } from "../../api/client";
import {
  memorySearch,
  kgSearch,
  type EntityCard,
  type MemorySearchHit,
} from "../../api/search";
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
}

export function useSearch(client: ApiClient, query: string): SearchResults {
  const [memoryHits, setMemoryHits] = useState<MemorySearchHit[]>([]);
  const [entityHits, setEntityHits] = useState<EntityCard[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

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

  return { memoryHits, entityHits, degraded, loading, error };
}

// ── Component ─────────────────────────────────────────────────────────────

export function SearchPage(): JSX.Element {
  const { client } = useAuth();
  const [rawQuery, setRawQuery] = useState("");
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);

  const query = useDebounced(rawQuery, 300);
  const { memoryHits, entityHits, degraded, loading, error } = useSearch(client, query);

  const handleInput = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setRawQuery(e.target.value);
    setSelectedEntityId(null);
  }, []);

  const handleEntitySelect = useCallback((entityId: string) => {
    setSelectedEntityId((prev) => (prev === entityId ? null : entityId));
  }, []);

  return (
    <section className="lumogis-search" aria-label="Memory search">
      <header className="lumogis-search__header">
        <h1 className="lumogis-search__title">Search</h1>
        <div className="lumogis-search__input-wrap">
          <input
            type="search"
            className="lumogis-search__input"
            placeholder="Search memories and entities…"
            value={rawQuery}
            onChange={handleInput}
            aria-label="Search query"
            aria-busy={loading}
            autoComplete="off"
            spellCheck={false}
          />
          {loading && (
            <span className="lumogis-search__spinner" aria-hidden="true" />
          )}
        </div>
      </header>

      {error && (
        <p className="lumogis-search__error" role="alert">
          {error}
        </p>
      )}

      {degraded && !error && (
        <p className="lumogis-search__degraded" role="status">
          Memory search is degraded — results may be incomplete.
        </p>
      )}

      <div className="lumogis-search__body">
        {/* Memory hits */}
        <section
          className="lumogis-search__col"
          aria-label="Memory results"
        >
          <h2 className="lumogis-search__col-title">Memories</h2>
          {!query.trim() && (
            <p className="lumogis-search__empty">Type to search your memories.</p>
          )}
          {query.trim() && memoryHits.length === 0 && !loading && (
            <p className="lumogis-search__empty">No memory hits.</p>
          )}
          <ul className="lumogis-search__hits" role="list">
            {memoryHits.map((hit) => (
              <li key={hit.id} className="lumogis-search__hit lumogis-search__hit--memory">
                <div className="lumogis-search__hit-title">
                  {hit.title ?? hit.id}
                </div>
                {hit.snippet && (
                  <p className="lumogis-search__hit-snippet">{hit.snippet}</p>
                )}
                <div className="lumogis-search__hit-meta">
                  <ScopePill scope={hit.scope} />
                  {hit.source && (
                    <span className="lumogis-search__hit-source">{hit.source}</span>
                  )}
                  <span className="lumogis-search__hit-score">
                    {(hit.score * 100).toFixed(0)}%
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* Entity hits */}
        <section
          className="lumogis-search__col"
          aria-label="Entity results"
        >
          <h2 className="lumogis-search__col-title">Entities</h2>
          {!query.trim() && (
            <p className="lumogis-search__empty">Type to search entities.</p>
          )}
          {query.trim() && entityHits.length === 0 && !loading && (
            <p className="lumogis-search__empty">No entity hits.</p>
          )}
          <ul className="lumogis-search__hits" role="list">
            {entityHits.map((entity) => (
              <li key={entity.entity_id} className="lumogis-search__hit lumogis-search__hit--entity">
                <button
                  type="button"
                  className={`lumogis-search__entity-btn${
                    selectedEntityId === entity.entity_id
                      ? " lumogis-search__entity-btn--active"
                      : ""
                  }`}
                  onClick={() => handleEntitySelect(entity.entity_id)}
                  aria-expanded={selectedEntityId === entity.entity_id}
                >
                  <span className="lumogis-search__entity-name">{entity.name}</span>
                  {entity.type && (
                    <span className="lumogis-search__entity-type">{entity.type}</span>
                  )}
                  <ScopePill scope={entity.scope} />
                </button>

                {selectedEntityId === entity.entity_id && (
                  <EntityCardPanel
                    entityId={entity.entity_id}
                    client={client}
                    initialCard={entity}
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

// ── Scope pill ────────────────────────────────────────────────────────────

function ScopePill({ scope }: { scope: string }): JSX.Element {
  return (
    <span
      className={`lumogis-scope-pill lumogis-scope-pill--${scope}`}
      aria-label={`Scope: ${scope}`}
    >
      {scope}
    </span>
  );
}
