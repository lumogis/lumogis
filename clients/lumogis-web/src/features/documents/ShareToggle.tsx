// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-document household share control (LUM-157).
//
// Owner view: an interactive toggle that publishes (with a plain-language
// confirm — sharing is a privacy boundary) or unpublishes the document,
// disabled while a share job is in flight, surfacing errors.
// Non-owner view: a read-only "Shared with your household" indicator — a
// member never gets a mutation affordance (the server also enforces this).

import { useState } from "react";

import type { ApiClient } from "../../api/client";
import { isShared, type ShareStatus } from "../../api/documents";
import { shareStatusLabel, useShareDocument, useUnshareDocument } from "./useDocuments";

export interface ShareToggleProps {
  client: ApiClient;
  documentId: number;
  displayName: string;
  shareStatus: ShareStatus | undefined;
  isOwner: boolean;
  // LUM-585 — attribution label for the non-owner indicator ("Shared by {member}").
  // Null falls back to the generic "Shared with your household".
  sharedBy?: string | null;
}

export function ShareToggle({
  client,
  documentId,
  displayName,
  shareStatus,
  isOwner,
  sharedBy,
}: ShareToggleProps): JSX.Element {
  const shareMutation = useShareDocument(client);
  const unshareMutation = useUnshareDocument(client);
  const [error, setError] = useState<string | null>(null);

  const shared = isShared(shareStatus);
  const transient = shareStatus === "sharing" || shareStatus === "unsharing";
  const pending = shareMutation.isPending || unshareMutation.isPending || transient;

  // Non-owner: read-only indicator, no mutation affordance. Attributed to the
  // sharing member when a label is available (LUM-585), else the generic copy.
  if (!isOwner) {
    return (
      <div className="lumogis-share-toggle" data-testid="share-indicator">
        <span className="lumogis-share-toggle__readonly">
          {sharedBy ? `Shared by ${sharedBy}` : "Shared with your household"}
        </span>
      </div>
    );
  }

  const handleToggle = async () => {
    setError(null);
    try {
      if (shared) {
        await unshareMutation.mutateAsync(documentId);
      } else {
        const ok = window.confirm(
          `Share "${displayName}" with your household? Everyone in your ` +
            `household will be able to search and read it. You can unshare anytime.`,
        );
        if (!ok) return;
        await shareMutation.mutateAsync(documentId);
      }
    } catch {
      setError(
        shared
          ? "Couldn't stop sharing this document. Please try again."
          : "Couldn't share this document. Please try again.",
      );
    }
  };

  return (
    <div className="lumogis-share-toggle" data-testid="share-toggle">
      <label className="lumogis-share-toggle__label">
        <input
          type="checkbox"
          role="switch"
          checked={shared}
          disabled={pending}
          aria-label={`Share "${displayName}" with your household`}
          onChange={() => void handleToggle()}
        />
        <span>{shareStatusLabel(shareStatus)}</span>
      </label>
      {shared && !transient ? (
        <p className="lumogis-share-toggle__hint">
          Everyone in your household can find and read this.
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
