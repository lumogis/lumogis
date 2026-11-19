// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-entity household share control (LUM-581).
//
// Owner view: an interactive toggle that publishes (with a plain-language
// AlertDialog confirm — sharing is a privacy boundary) or unpublishes the entity, disabled
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
import { Alert, AlertDescription } from "../../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../../components/ui/alert-dialog";
import { Switch } from "../../components/ui/switch";
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
  const [confirmOpen, setConfirmOpen] = useState(false);

  const shared = isEntityShared(shareStatus);
  const pending = shareMutation.isPending || unshareMutation.isPending;

  // Non-owner: read-only indicator, no mutation affordance.
  if (!isOwner) {
    return (
      <div data-testid="entity-share-indicator">
        <span className="text-sm">Shared with your household</span>
      </div>
    );
  }

  const run = async (publish: boolean) => {
    setError(null);
    try {
      if (publish) {
        await shareMutation.mutateAsync(entityId);
      } else {
        await unshareMutation.mutateAsync(entityId);
      }
      onChanged?.();
    } catch {
      setError(
        publish
          ? "Couldn't share this entity. You can only share your own items."
          : "Couldn't stop sharing this entity. Please try again.",
      );
    }
  };

  // Unsharing needs no confirm; sharing is a privacy boundary and asks first.
  const handleToggle = () => {
    if (shared) {
      void run(false);
    } else {
      setConfirmOpen(true);
    }
  };

  return (
    <div className="flex flex-col gap-1.5" data-testid="entity-share-toggle">
      <div className="flex items-center gap-2">
        <Switch
          checked={shared}
          disabled={pending}
          aria-label={`Share "${displayName}" with your household`}
          onCheckedChange={handleToggle}
        />
        <span className="text-sm">{entityShareStatusLabel(shareStatus)}</span>
      </div>
      {shared ? (
        <p className="m-0 text-xs text-muted-foreground">
          Everyone in your household can find this.
        </p>
      ) : null}
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Share with your household?</AlertDialogTitle>
            <AlertDialogDescription>
              {`Share "${displayName}" with your household? Everyone in your household will be able to find it. You can unshare anytime.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmOpen(false);
                void run(true);
              }}
            >
              Share
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
