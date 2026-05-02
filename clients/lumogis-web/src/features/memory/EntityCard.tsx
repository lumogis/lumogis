// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Entity card — parent plan §"Phase 1 Pass 1.3 item 10".
//
// EntityCardPanel fetches the full entity record (GET /api/v1/kg/entities/{id})
// and its first-degree relations (GET /api/v1/kg/entities/{id}/related) when
// an entity is selected in SearchPage. The `initialCard` prop (the lightweight
// EntityCard from the search response) renders immediately so there is no
// flicker between selection and full-detail load.
/* eslint-disable react-refresh/only-export-components */

import { useEffect, useRef, useState } from "react";

import type { ApiClient } from "../../api/client";
import {
  getEntity,
  getRelatedEntities,
  type EntityCard,
  type RelatedEntity,
} from "../../api/search";

// ── Hook ──────────────────────────────────────────────────────────────────

export interface EntityCardState {
  card: EntityCard | null;
  related: RelatedEntity[];
  loading: boolean;
  error: string | null;
}

export function useEntityCard(
  client: ApiClient,
  entityId: string | null,
): EntityCardState {
  const [card, setCard] = useState<EntityCard | null>(null);
  const [related, setRelated] = useState<RelatedEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!entityId) {
      setCard(null);
      setRelated([]);
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
      getEntity(client, entityId, ctrl.signal),
      getRelatedEntities(client, entityId, 20, ctrl.signal),
    ]).then(([cardRes, relRes]) => {
      if (ctrl.signal.aborted) return;

      if (cardRes.status === "fulfilled") {
        setCard(cardRes.value);
      } else {
        setError("Entity not found or unavailable.");
        setCard(null);
      }

      if (relRes.status === "fulfilled") {
        setRelated(relRes.value.related);
      } else {
        setRelated([]);
      }

      setLoading(false);
    });

    return () => {
      ctrl.abort();
    };
  }, [client, entityId]);

  return { card, related, loading, error };
}

// ── Component ─────────────────────────────────────────────────────────────

export interface EntityCardPanelProps {
  entityId: string;
  client: ApiClient;
  /** Initial data from the search result — shown while the full card loads. */
  initialCard?: EntityCard;
}

export function EntityCardPanel({
  entityId,
  client,
  initialCard,
}: EntityCardPanelProps): JSX.Element {
  const { card, related, loading, error } = useEntityCard(client, entityId);

  const displayed = card ?? initialCard ?? null;

  return (
    <article className="lumogis-entity-card" aria-label={`Entity: ${displayed?.name ?? entityId}`}>
      {loading && !displayed && (
        <div className="lumogis-entity-card__loading" aria-live="polite">
          Loading…
        </div>
      )}

      {error && (
        <p className="lumogis-entity-card__error" role="alert">
          {error}
        </p>
      )}

      {displayed && (
        <>
          <header className="lumogis-entity-card__header">
            <h3 className="lumogis-entity-card__name">{displayed.name}</h3>
            {displayed.type && (
              <span className="lumogis-entity-card__type">{displayed.type}</span>
            )}
            <span
              className={`lumogis-scope-pill lumogis-scope-pill--${displayed.scope}`}
              aria-label={`Scope: ${displayed.scope}`}
            >
              {displayed.scope}
            </span>
          </header>

          {displayed.summary && (
            <p className="lumogis-entity-card__summary">{displayed.summary}</p>
          )}

          {displayed.aliases.length > 0 && (
            <section className="lumogis-entity-card__section">
              <h4 className="lumogis-entity-card__section-title">Also known as</h4>
              <ul className="lumogis-entity-card__aliases" role="list">
                {displayed.aliases.map((a) => (
                  <li key={a} className="lumogis-entity-card__alias">{a}</li>
                ))}
              </ul>
            </section>
          )}

          {displayed.sources.length > 0 && (
            <section className="lumogis-entity-card__section">
              <h4 className="lumogis-entity-card__section-title">Sources</h4>
              <ul className="lumogis-entity-card__sources" role="list">
                {displayed.sources.map((s) => (
                  <li key={s} className="lumogis-entity-card__source">{s}</li>
                ))}
              </ul>
            </section>
          )}

          {related.length > 0 && (
            <section className="lumogis-entity-card__section">
              <h4 className="lumogis-entity-card__section-title">Related</h4>
              <ul className="lumogis-entity-card__related" role="list">
                {related.map((r) => (
                  <li key={r.entity_id} className="lumogis-entity-card__related-item">
                    <span className="lumogis-entity-card__related-name">{r.name}</span>
                    <span className="lumogis-entity-card__related-relation">
                      {r.relation}
                    </span>
                    {r.weight !== null && r.weight !== undefined && (
                      <span className="lumogis-entity-card__related-weight">
                        {r.weight.toFixed(2)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {loading && (
            <div className="lumogis-entity-card__refreshing" aria-live="polite">
              Refreshing…
            </div>
          )}
        </>
      )}
    </article>
  );
}
