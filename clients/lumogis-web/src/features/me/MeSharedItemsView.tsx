// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Settings → My shared items (LUM-583): everything the member has shared with
// the household, in one place, with per-row unshare. Read-only aggregate +
// reuse of the owner-only per-type unpublish route — this page never shares.
// Rebuilt on the shared components in LUM-993 plan B Pass 6: rows stack on a
// phone, "Stop sharing" sits in the row's "…" menu and is confirmed first.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  listMySharedItems,
  unshareMyItem,
  type SharedItem,
} from "../../api/sharedItems";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { RowActions } from "../../components/page/RowActions";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { formatDateTime } from "../../util/formatTimestamp";

const RESOURCE_LABELS: Record<SharedItem["resource_type"], string> = {
  notes: "note",
  audio_memos: "audio memo",
  sessions: "session",
  files: "document",
  entities: "entity",
  signals: "signal",
};

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    try {
      const parsed = JSON.parse(e.detail) as {
        detail?: { message?: unknown };
        message?: unknown;
      };
      const msg = parsed?.detail?.message ?? parsed?.message;
      if (typeof msg === "string" && msg.length > 0) return msg;
    } catch {
      /* not JSON — fall back to the raw detail */
    }
    return e.detail;
  }
  if (e instanceof Error) return e.message;
  return "Request failed";
}

function rowKey(item: SharedItem): string {
  return `${item.resource_type}:${item.resource_id}`;
}

export function MeSharedItemsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<SharedItem | null>(null);

  const listQ = useQuery({
    queryKey: ["me", "shared-items"],
    queryFn: () => listMySharedItems(client),
  });

  const unshareM = useMutation({
    mutationFn: (item: SharedItem) =>
      unshareMyItem(client, item.resource_type, item.resource_id),
    onSuccess: () => {
      toast("Stopped sharing.");
      void qc.invalidateQueries({ queryKey: ["me", "shared-items"] });
    },
    onError: (e) => setErr(errMsg(e)),
  });

  const items = listQ.data?.items ?? [];
  const confirmType = confirming ? RESOURCE_LABELS[confirming.resource_type] : "";

  return (
    <section data-testid="me-shared-items">
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        Everything you&apos;ve shared with your household. Stopping a share removes the
        household copy; your private original stays with you and you can share
        it again anytime.
      </p>

      {err && (
        <Alert variant="destructive" className="mb-4 max-w-2xl">
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      )}

      {listQ.isPending && (
        <LoadingPlaceholder label="Loading what you've shared…" className="max-w-2xl">
          <SkeletonText lines={4} />
        </LoadingPlaceholder>
      )}
      {listQ.isError && (
        <ErrorState message="Lumogis couldn't load your shared items." onRetry={() => void listQ.refetch()} />
      )}
      {listQ.isSuccess && items.length === 0 && (
        <EmptyState
          title="Nothing shared yet"
          helperText="You haven't shared anything with your household yet. Share a document or a conversation and it will appear here."
        />
      )}

      {listQ.isSuccess && items.length > 0 && (
        <PageSection title="Shared by you">
          <Table>
            <TableHeader className="max-sm:hidden">
              <TableRow>
                <TableHead>Item</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Shared</TableHead>
                <TableHead className="w-12">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const key = rowKey(item);
                const type = RESOURCE_LABELS[item.resource_type];
                const name = item.label ?? `Untitled ${type}`;
                return (
                  <TableRow key={key} data-testid={`shared-row-${key}`} className="max-sm:flex max-sm:flex-wrap max-sm:items-center">
                    <TableCell className="whitespace-normal break-words max-sm:w-full max-sm:font-medium sm:max-w-64">
                      {name}
                    </TableCell>
                    <TableCell className="capitalize text-muted-foreground">{type}</TableCell>
                    <TableCell className="text-muted-foreground max-sm:flex-1">
                      {item.shared_at ? formatDateTime(item.shared_at, { withTime: false }) : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <RowActions
                        label={`More actions for ${name}`}
                        items={[
                          {
                            label: "Stop sharing",
                            destructive: true,
                            onSelect: () => {
                              setErr(null);
                              setConfirming(item);
                            },
                          },
                        ]}
                      />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </PageSection>
      )}

      <AlertDialog open={confirming !== null} onOpenChange={(open) => (open ? undefined : setConfirming(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Stop sharing this {confirmType}?</AlertDialogTitle>
            <AlertDialogDescription>
              The household copy goes away. Your private original stays with you, and you can share it again
              anytime.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                if (confirming) unshareM.mutate(confirming);
                setConfirming(null);
              }}
            >
              Stop sharing
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
