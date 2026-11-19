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
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, Skeleton, SkeletonText } from "../../components/page/LoadingState";
import { buildAskAboutQuery, navigateToChatWithPrefill } from "../wow/askAboutEntity";
import {
  getEntity,
  getRelatedEntities,
  hideEntity,
  unhideEntity,
  type EntityCard,
  type EntityDeleteResponse,
  type RelatedEntity,
} from "../../api/search";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { EntityDeleteDialog } from "../entities/EntityDeleteDialog";
import { EntityEditDialog } from "../entities/EntityEditDialog";
import { EntityKindDialog, kindNoun, withArticle } from "../entities/EntityKindDialog";
import { EntityMergeDialog } from "../entities/EntityMergeDialog";
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
  /**
   * LUM-161 — offer an "Edit names" button (owner + personal row only). Default
   * false so SearchPage is unchanged.
   */
  allowEdit?: boolean;
  /** Called after a successful rename, once the card has been re-fetched. */
  onEdited?: () => void;
  /** Called whenever the full card (re)loads — lets the detail page learn ownership. */
  onCardLoaded?: (card: EntityCard) => void;
  /**
   * LUM-966 — called after the owner deleted the entry, with the server's answer
   * (blocked and not-blocked names). The entry no longer exists, so the caller
   * moves on (the detail page goes back to the list with a notice).
   */
  onDeleted?: (response: EntityDeleteResponse) => void;
}

export function EntityCardPanel({
  entityId,
  client,
  initialCard,
  onShareChanged,
  allowEdit = false,
  onEdited,
  onCardLoaded,
  onDeleted,
}: EntityCardPanelProps): JSX.Element {
  const navigate = useNavigate();
  const { card, related, loading, error, reload } = useEntityCard(client, entityId);
  const [editing, setEditing] = useState(false);
  const [changingKind, setChangingKind] = useState(false);
  const [merging, setMerging] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [hideBusy, setHideBusy] = useState(false);
  const [hideError, setHideError] = useState(false);
  const onCardLoadedRef = useRef(onCardLoaded);
  onCardLoadedRef.current = onCardLoaded;
  useEffect(() => {
    if (card) onCardLoadedRef.current?.(card);
  }, [card]);

  const displayed = card ?? initialCard ?? null;
  // The Edit button needs the full card (it carries `version`), not the search stub.
  const canEdit =
    allowEdit && !!card && !!card.is_owner && card.scope === "personal" && card.version != null;
  // LUM-966: an entry another member owns can be hidden from the caller's own view
  // (not deleted). The full card is authoritative for "hidden"; a search stub is not.
  const canHide = !!displayed && displayed.is_owner === false;
  const isHidden = !!card && card.hidden === true;

  const setHidden = async (hide: boolean): Promise<void> => {
    if (!displayed || hideBusy) return;
    setHideBusy(true);
    setHideError(false);
    try {
      if (hide) await hideEntity(client, displayed.entity_id);
      else await unhideEntity(client, displayed.entity_id);
      reload();
      onEdited?.();
    } catch {
      setHideError(true);
    } finally {
      setHideBusy(false);
    }
  };

  return (
    <article
      className="mt-2 flex flex-col gap-3 rounded-lg border border-border bg-card p-4 text-card-foreground"
      aria-label={`Entity: ${displayed?.name ?? entityId}`}
    >
      {loading && !displayed && (
        <LoadingPlaceholder label="Loading entity…">
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
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center gap-2">
            <span>{error}</span>
            <Button type="button" variant="outline" size="sm" onClick={reload}>
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {displayed && (
        <>
          {/* The household state is shown once, by the share control below
              (its label reads Personal or Shared); no separate scope badge. */}
          <header className="flex flex-wrap items-center gap-2">
            <h3 className="m-0 text-lg font-semibold">{displayed.name}</h3>
            {displayed.type && <Badge variant="secondary">{displayed.type}</Badge>}
          </header>

          {displayed.summary && (
            <p className="m-0 text-sm text-muted-foreground">{displayed.summary}</p>
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

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              onClick={() => {
                navigateToChatWithPrefill(navigate, buildAskAboutQuery(displayed.name), {
                  wowDismissOnSend: true,
                });
              }}
            >
              Ask Lumogis about {displayed.name}
            </Button>
            {canEdit && (
              <Button type="button" variant="outline" onClick={() => setEditing(true)}>
                Edit names
              </Button>
            )}
            {canEdit && (
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      type="button"
                      variant="outline"
                      aria-label={`This is wrong… (${displayed.name})`}
                      data-testid="entity-wrong-menu"
                    />
                  }
                >
                  This is wrong…
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-auto min-w-48">
                  <DropdownMenuItem onClick={() => setChangingKind(true)}>
                    It&apos;s not {withArticle(kindNoun(displayed.type))}, it&apos;s a…
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setMerging(true)}>
                    It&apos;s the same as…
                  </DropdownMenuItem>
                  <DropdownMenuItem variant="destructive" onClick={() => setDeleting(true)}>
                    Delete…
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {canHide && !isHidden && (
              <Button
                type="button"
                variant="outline"
                disabled={hideBusy}
                onClick={() => void setHidden(true)}
              >
                Hide from my list
              </Button>
            )}
          </div>

          {canHide && isHidden && (
            <p className="m-0 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              Hidden from your list.
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={hideBusy}
                onClick={() => void setHidden(false)}
              >
                Show again
              </Button>
            </p>
          )}
          {hideError && (
            <Alert variant="destructive">
              <AlertDescription>Couldn&apos;t change that. Try again.</AlertDescription>
            </Alert>
          )}

          {displayed.aliases.length > 0 && (
            <section className="space-y-1">
              <h4 className="m-0 text-sm font-medium">Also known as</h4>
              <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" role="list">
                {displayed.aliases.map((a) => (
                  <li key={a}>
                    <Badge variant="outline">{a}</Badge>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {displayed.sources.length > 0 && (
            <section className="space-y-1">
              <h4 className="m-0 text-sm font-medium">Sources</h4>
              <ul className="m-0 list-none space-y-0.5 p-0 text-sm text-muted-foreground" role="list">
                {displayed.sources.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </section>
          )}

          {related.length > 0 && (
            <section className="space-y-1">
              <h4 className="m-0 text-sm font-medium">Related</h4>
              <ul className="m-0 list-none space-y-0.5 p-0 text-sm" role="list">
                {related.map((r) => (
                  <li key={r.entity_id} className="flex flex-wrap items-baseline gap-2">
                    <span>{r.name}</span>
                    <span className="text-muted-foreground">{r.relation}</span>
                    {r.weight !== null && r.weight !== undefined && (
                      <span className="text-xs text-muted-foreground">{r.weight.toFixed(2)}</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {loading && (
            <div className="text-xs text-muted-foreground" aria-live="polite">
              Refreshing…
            </div>
          )}

          {changingKind && canEdit && card && (
            <EntityKindDialog
              card={card}
              client={client}
              onClose={() => setChangingKind(false)}
              onReload={() => {
                setChangingKind(false);
                reload();
              }}
              onSaved={() => {
                setChangingKind(false);
                reload();
                onEdited?.();
              }}
              onMergedAway={(survivorId) => {
                setChangingKind(false);
                onEdited?.();
                navigate(`/entities/${encodeURIComponent(survivorId)}`);
              }}
            />
          )}

          {merging && canEdit && card && (
            <EntityMergeDialog
              entry={card}
              client={client}
              onClose={() => setMerging(false)}
              onReload={() => {
                setMerging(false);
                reload();
                onEdited?.();
              }}
              onMerged={() => {
                setMerging(false);
                reload();
                onEdited?.();
              }}
            />
          )}

          {deleting && canEdit && card && (
            <EntityDeleteDialog
              card={card}
              client={client}
              onClose={() => setDeleting(false)}
              onDeleted={(res) => {
                setDeleting(false);
                onDeleted?.(res);
              }}
            />
          )}

          {editing && canEdit && card && (
            <EntityEditDialog
              card={card}
              client={client}
              onClose={() => setEditing(false)}
              onReload={() => {
                setEditing(false);
                reload();
              }}
              onSaved={() => {
                setEditing(false);
                reload();
                onEdited?.();
              }}
            />
          )}
        </>
      )}
    </article>
  );
}
