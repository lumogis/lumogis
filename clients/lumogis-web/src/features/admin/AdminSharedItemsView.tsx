// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin household-sharing governance (LUM-584): review every item shared with
// the household and retract one on a member's behalf (retract-only, audited).
// Distinct from the owner-only unshare each member has on their own items —
// this is admin governance, server-gated by require_admin.
// Rebuilt on the shared components in LUM-993 plan B Pass 6.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  adminUnshare,
  fetchAdminSharedItems,
  type AdminSharedItem,
} from "../../api/adminSharedItems";
import { ApiError } from "../../api/client";
import { useAuth, useUser } from "../../auth/AuthProvider";
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

const RESOURCE_LABELS: Record<AdminSharedItem["resource_type"], string> = {
  notes: "note",
  audio_memos: "audio memo",
  sessions: "session",
  files: "document",
  entities: "entity",
  signals: "signal",
};

// The API returns structured errors as `detail: { error, message }`. The
// client's safeReadDetail can't unwrap a non-string detail, so ApiError.detail
// arrives as a JSON blob — pull out the human-authored `message` when present
// (governance failures like "Couldn't fully unshare — please retry" matter to
// the admin), falling back to the raw text.
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
      /* not JSON — use the raw detail below */
    }
    return e.detail;
  }
  if (e instanceof Error) return e.message;
  return "Request failed";
}

function rowKey(item: AdminSharedItem): string {
  return `${item.resource_type}:${item.resource_id}`;
}

function ownerLabel(item: AdminSharedItem): string {
  return item.source_owner_id ?? "another member";
}

export function AdminSharedItemsView(): JSX.Element | null {
  const { client } = useAuth();
  const user = useUser();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<AdminSharedItem | null>(null);

  const isAdmin = user?.role === "admin";

  const listQ = useQuery({
    queryKey: ["admin", "shared-items"],
    queryFn: () => fetchAdminSharedItems(client),
    enabled: isAdmin,
  });

  const unshareM = useMutation({
    mutationFn: (item: AdminSharedItem) =>
      adminUnshare(client, item.resource_type, item.resource_id),
    onSuccess: () => {
      toast("Stopped sharing.");
      void qc.invalidateQueries({ queryKey: ["admin", "shared-items"] });
    },
    onError: (e) => setErr(errMsg(e)),
  });

  // Defense-in-depth: the /admin route already gates the whole subtree, but the
  // retract affordance must never render for a non-admin (mirrors the server
  // gate). Placed after the hooks so hook order stays stable.
  if (user && !isAdmin) return null;

  const items = listQ.data?.items ?? [];

  return (
    <section data-testid="admin-shared-items">
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        Everything members have shared with the household. As an admin you can
        stop a share on a member&apos;s behalf. This removes the household
        copy only; the owner keeps their private original and can share it again.
      </p>

      {err && (
        <Alert variant="destructive" className="mb-4 max-w-2xl">
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      )}

      {listQ.isPending && (
        <LoadingPlaceholder label="Loading shared items…" className="max-w-2xl">
          <SkeletonText lines={4} />
        </LoadingPlaceholder>
      )}
      {listQ.isError && (
        <ErrorState message="Lumogis couldn't load the shared items." onRetry={() => void listQ.refetch()} />
      )}
      {listQ.isSuccess && items.length === 0 && (
        <EmptyState
          title="Nothing shared yet"
          helperText="Nothing is shared with the household yet. When a member shares something it will appear here."
        />
      )}

      {listQ.isSuccess && items.length > 0 && (
        <PageSection title="Shared with the household">
          <Table>
            <TableHeader className="max-sm:hidden">
              <TableRow>
                <TableHead>Item</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Shared by</TableHead>
                <TableHead className="w-12">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const key = rowKey(item);
                const type = RESOURCE_LABELS[item.resource_type];
                const name = item.label ?? item.resource_id;
                return (
                  <TableRow key={key} data-testid={`shared-row-${key}`} className="max-sm:flex max-sm:flex-wrap max-sm:items-center">
                    <TableCell className="whitespace-normal break-words max-sm:w-full max-sm:font-medium sm:max-w-64">
                      {name}
                    </TableCell>
                    <TableCell className="capitalize text-muted-foreground">{type}</TableCell>
                    <TableCell className="text-muted-foreground max-sm:flex-1">{ownerLabel(item)}</TableCell>
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
            <AlertDialogTitle>
              {confirming
                ? `Stop sharing ${ownerLabel(confirming)}'s ${RESOURCE_LABELS[confirming.resource_type]}?`
                : "Stop sharing?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              The household copy goes away. The owner keeps their private original and can share it again.
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
