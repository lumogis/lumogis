// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Entry page (LUM-160/LUM-161): the entity card (with the owner-only "Edit
// names" dialog and, from LUM-966, Delete), a "Mentioned in" section listing what the
// viewer can open, and the owner-only History of merges with Undo (LUM-965).

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { EntityCardPanel } from "../memory/EntityCard";
import { EntityHistory } from "./EntityHistory";
import { EntityMentions } from "./EntityMentions";

export function EntityDetailPage(): JSX.Element {
  const { entityId } = useParams<{ entityId: string }>();
  const { client } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [isOwner, setIsOwner] = useState(true);
  // History is owner-only: wait for the card so a non-owner never asks for it (403).
  const [cardLoaded, setCardLoaded] = useState(false);
  // Bumped after an undo or a move so the card re-fetches (the survivor's names changed).
  const [cardKey, setCardKey] = useState(0);
  const refreshAll = (): void => {
    void queryClient.invalidateQueries({ queryKey: ["kg", "entities"] });
    setCardKey((k) => k + 1);
  };

  if (!entityId) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Missing entity id.</AlertDescription>
      </Alert>
    );
  }

  return (
    <section data-testid="entity-detail-page">
      <p className="mb-2">
        <Link to="/entities" className="text-accent-text underline underline-offset-2">
          ← What Lumogis knows
        </Link>
      </p>
      <EntityCardPanel
        key={cardKey}
        entityId={entityId}
        client={client}
        allowEdit
        onCardLoaded={(c) => {
          setIsOwner(c.is_owner ?? true);
          setCardLoaded(true);
        }}
        onEdited={refreshAll}
        onDeleted={(deleted) => {
          // The entry is gone: back to the list, which says what Lumogis won't learn.
          void queryClient.invalidateQueries({ queryKey: ["kg", "entities"] });
          navigate("/entities", { state: { deleted } });
        }}
      />
      <EntityMentions entityId={entityId} client={client} isOwner={isOwner} />
      <EntityHistory
        entityId={entityId}
        client={client}
        isOwner={cardLoaded && isOwner}
        onChanged={refreshAll}
      />
    </section>
  );
}
