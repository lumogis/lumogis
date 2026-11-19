// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Per-document household share control (LUM-157).
//
// Owner view: an interactive toggle that publishes (with a plain-language
// AlertDialog confirm — sharing is a privacy boundary) or unpublishes the document,
// disabled while a share job is in flight, surfacing errors.
// Non-owner view: a read-only "Shared with your household" indicator — a
// member never gets a mutation affordance (the server also enforces this).

import { useState } from "react";

import type { ApiClient } from "../../api/client";
import { isShared, type ShareStatus } from "../../api/documents";
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
  const [confirmOpen, setConfirmOpen] = useState(false);

  const shared = isShared(shareStatus);
  const transient = shareStatus === "sharing" || shareStatus === "unsharing";
  const pending = shareMutation.isPending || unshareMutation.isPending || transient;

  // Non-owner: read-only indicator, no mutation affordance. Attributed to the
  // sharing member when a label is available (LUM-585), else the generic copy.
  if (!isOwner) {
    return (
      <div data-testid="share-indicator">
        <span className="text-sm">
          {sharedBy ? `Shared by ${sharedBy}` : "Shared with your household"}
        </span>
      </div>
    );
  }

  const run = async (publish: boolean) => {
    setError(null);
    try {
      if (publish) {
        await shareMutation.mutateAsync(documentId);
      } else {
        await unshareMutation.mutateAsync(documentId);
      }
    } catch {
      setError(
        publish
          ? "Couldn't share this document. Please try again."
          : "Couldn't stop sharing this document. Please try again.",
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
    <div className="flex flex-col items-end gap-1.5" data-testid="share-toggle">
      <div className="flex items-center gap-2">
        <span className="text-sm font-normal">{shareStatusLabel(shareStatus)}</span>
        <Switch
          checked={shared}
          disabled={pending}
          aria-label={`Share "${displayName}" with your household`}
          onCheckedChange={handleToggle}
        />
      </div>
      {shared && !transient ? (
        <p className="m-0 text-xs font-normal text-muted-foreground">
          Everyone in your household can find and read this.
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
              {`Share "${displayName}" with your household? Everyone in your household will be able to search and read it. You can unshare anytime.`}
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
