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
import { useNavigate } from "react-router-dom";

import type { ApiClient } from "../../api/client";
import { ErrorState } from "../_shared/ErrorState";
import { LoadingPlaceholder, Skeleton, SkeletonText } from "../_shared/Skeleton";
import { buildAskAboutQuery, navigateToChatWithPrefill } from "../wow/askAboutEntity";
import {
  getEntity,
  getRelatedEntities,
  type EntityCard,
  type RelatedEntity,
} from "../../api/search";
import { EntityShareToggle } from "./EntityShareToggle";

// ── Hook ──────────────────────────────────────────────────────────────────

export interface EntityCardState {
  card: EntityCard | null;
  related: RelatedEntity[];
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useEntityCard(
  client: ApiClient,
  entityId: string | null,
): EntityCardState {
  const [card, setCard] = useState<EntityCard | null>(null);
  const [related, setRelated] = useState<RelatedEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
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
  }, [client, entityId, reloadKey]);

  return { card, related, loading, error, reload: () => setReloadKey((k) => k + 1) };
}

// ── Component ─────────────────────────────────────────────────────────────

export interface EntityCardPanelProps {
  entityId: string;
  client: ApiClient;
  /** Initial data from the search result — shown while the full card loads. */
  initialCard?: EntityCard;
  /** Re-run the search entity list after publish/unpublish (LUM-581 list badge). */
  onShareChanged?: () => void;
}

export function EntityCardPanel({
  entityId,
  client,
  initialCard,
  onShareChanged,
}: EntityCardPanelProps): JSX.Element {
  const navigate = useNavigate();
  const { card, related, loading, error, reload } = useEntityCard(client, entityId);

  const displayed = card ?? initialCard ?? null;

  return (
    <article className="lumogis-entity-card" aria-label={`Entity: ${displayed?.name ?? entityId}`}>
      {loading && !displayed && (
        <LoadingPlaceholder label="Loading entity…" className="lumogis-entity-card__loading">
          <Skeleton width="55%" height="1.3rem" />
          <SkeletonText lines={3} />
        </LoadingPlaceholder>
      )}

      {error && !displayed && (
        <ErrorState
          title="Couldn't load this entity"
          message={error}
          onRetry={reload}
          doctorHint={false}
        />
      )}

      {/* Partial data from search (initialCard) is shown, but the full fetch
          failed — surface a compact, non-blocking error with a retry rather
          than silently leaving stale data. */}
      {error && displayed && (
        <div className="lumogis-entity-card__error" role="alert">
          <span>{error}</span>{" "}
          <button type="button" onClick={reload}>
            Try again
          </button>
        </div>
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

          <EntityShareToggle
            client={client}
            entityId={displayed.entity_id}
            displayName={displayed.name}
            shareStatus={displayed.share_status}
            isOwner={displayed.is_owner ?? true}
            onChanged={() => {
              reload();
              onShareChanged?.();
            }}
          />

          <p className="lumogis-entity-card__actions">
            <button
              type="button"
              className="lumogis-entity-card__ask"
              onClick={() => {
                navigateToChatWithPrefill(navigate, buildAskAboutQuery(displayed.name), {
                  wowDismissOnSend: true,
                });
              }}
            >
              Ask Lumogis about {displayed.name}
            </button>
          </p>

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
