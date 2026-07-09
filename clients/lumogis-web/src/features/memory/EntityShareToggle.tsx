// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-entity household share control (LUM-581).
//
// Owner view: an interactive toggle that publishes (with a plain-language
// confirm — sharing is a privacy boundary) or unpublishes the entity, disabled
// while the request is in flight, surfacing errors and preserving prior state
// on failure.
// Non-owner view: a read-only "Shared with your household" indicator — a member
// never gets a mutation affordance (the server also enforces this).
//
// Mirrors `features/documents/ShareToggle.tsx` but SYNCHRONOUS: entity publish
// is a single-point 200/204, not a 202 background job, so there is no
// share-progress / transient state.

import { useState } from "react";

import type { ApiClient } from "../../api/client";
import { isEntityShared, type EntityShareStatus } from "../../api/search";
import {
  entityShareStatusLabel,
  usePublishEntity,
  useUnpublishEntity,
} from "./useEntitySharing";

export interface EntityShareToggleProps {
  client: ApiClient;
  entityId: string;
  displayName: string;
  shareStatus: EntityShareStatus | undefined;
  isOwner: boolean;
  /** Re-run the caller's fetch after a successful publish/unpublish. */
  onChanged?: () => void;
}

export function EntityShareToggle({
  client,
  entityId,
  displayName,
  shareStatus,
  isOwner,
  onChanged,
}: EntityShareToggleProps): JSX.Element {
  const shareMutation = usePublishEntity(client);
  const unshareMutation = useUnpublishEntity(client);
  const [error, setError] = useState<string | null>(null);

  const shared = isEntityShared(shareStatus);
  const pending = shareMutation.isPending || unshareMutation.isPending;

  // Non-owner: read-only indicator, no mutation affordance.
  if (!isOwner) {
    return (
      <div className="lumogis-share-toggle" data-testid="entity-share-indicator">
        <span className="lumogis-share-toggle__readonly">
          Shared with your household
        </span>
      </div>
    );
  }

  const handleToggle = async () => {
    setError(null);
    try {
      if (shared) {
        await unshareMutation.mutateAsync(entityId);
      } else {
        const ok = window.confirm(
          `Share "${displayName}" with your household? Everyone in your ` +
            `household will be able to find it. You can unshare anytime.`,
        );
        if (!ok) return;
        await shareMutation.mutateAsync(entityId);
      }
      onChanged?.();
    } catch {
      setError(
        shared
          ? "Couldn't stop sharing this entity. Please try again."
          : "Couldn't share this entity. You can only share your own items.",
      );
    }
  };

  return (
    <div className="lumogis-share-toggle" data-testid="entity-share-toggle">
      <label className="lumogis-share-toggle__label">
        <input
          type="checkbox"
          role="switch"
          checked={shared}
          disabled={pending}
          aria-label={`Share "${displayName}" with your household`}
          onChange={() => void handleToggle()}
        />
        <span>{entityShareStatusLabel(shareStatus)}</span>
      </label>
      {shared ? (
        <p className="lumogis-share-toggle__hint">
          Everyone in your household can find this.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="lumogis-share-toggle__error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
