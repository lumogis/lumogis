// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { useAuth, useUser } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import type { McpTokenAdminRow } from "../../api/mcpTokens";
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
import { Badge } from "../../components/ui/badge";
import { formatDateTime } from "../../util/formatTimestamp";
import { UserPicker } from "../_shared/UserPicker";
import { accessLabel } from "../me/mcpTokenDisplay";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return "Could not revoke the key. Try again.";
}

// Admins list + revoke other users' MCP tokens; minting is self-service only
// (each user mints their own at /me/mcp-tokens — LUM-291 D12). The former
// admin Mint button POSTed to a route that never existed (405) and was removed
// in LUM-530.
export function AdminMcpTokensView(): JSX.Element {
  const { client } = useAuth();
  const me = useUser();
  const isAdmin = me?.role === "admin";
  const qc = useQueryClient();
  const [userId, setUserId] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<McpTokenAdminRow | null>(null);

  const base = userId
    ? `/api/v1/admin/users/${encodeURIComponent(userId)}/mcp-tokens`
    : null;

  const listQ = useQuery({
    queryKey: ["mcp", "admin", userId],
    queryFn: () => client.getJson<McpTokenAdminRow[]>(base!),
    enabled: Boolean(base),
  });

  const delM = useMutation({
    mutationFn: (id: string) => client.delete(`${base!}/${encodeURIComponent(id)}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["mcp", "admin", userId] });
      toast("Key revoked.");
    },
    onError: (e) => setErr(errMsg(e)),
  });

  return (
    <div className="flex flex-col gap-2">
      {err && (
        <Alert variant="destructive" className="max-w-2xl">
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      )}
      <PageSection
        title="Keys for one person"
        description="Choose a person to see the keys that let AI apps reach their Lumogis. People create their own keys; you can take one away."
      >
        <div className="flex flex-col gap-4">
          <UserPicker value={userId} onChange={setUserId} isAdmin={isAdmin} />
          {!userId && <p className="m-0 text-sm text-muted-foreground">Choose a person to list their keys.</p>}
          {userId && listQ.isPending && (
            <LoadingPlaceholder label="Loading their keys…">
              <SkeletonText lines={3} />
            </LoadingPlaceholder>
          )}
          {userId && listQ.isError && (
            <ErrorState message="Lumogis couldn't load their keys." onRetry={() => void listQ.refetch()} />
          )}
          {userId && listQ.isSuccess &&
            ((listQ.data ?? []).length === 0 ? (
              <EmptyState title="No keys" helperText="This person has not created a key for an AI app." />
            ) : (
              <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
                {listQ.data?.map((t) => (
                  <li key={t.id} className="flex min-h-12 flex-wrap items-center gap-2 py-1.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-sm font-medium break-words">
                        {t.label}
                        {t.revoked_at ? <Badge variant="secondary">Revoked</Badge> : null}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        Created {formatDateTime(t.created_at)}. {accessLabel(t.scopes)}.
                      </div>
                    </div>
                    <RowActions
                      label={`Actions for ${t.label}`}
                      items={[
                        {
                          label: "Revoke",
                          destructive: true,
                          disabled: Boolean(t.revoked_at),
                          onSelect: () => setRevoking(t),
                        },
                      ]}
                    />
                  </li>
                ))}
              </ul>
            ))}
        </div>
      </PageSection>

      <AlertDialog open={revoking !== null} onOpenChange={(open) => (open ? undefined : setRevoking(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke {revoking?.label}?</AlertDialogTitle>
            <AlertDialogDescription>
              The app using this key loses access to their Lumogis straight away. They can create a new key later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                if (revoking) {
                  setErr(null);
                  delM.mutate(revoking.id);
                }
                setRevoking(null);
              }}
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
