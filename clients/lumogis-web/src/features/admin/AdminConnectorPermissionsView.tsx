// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "What Lumogis may do" for the household (admin). Rebuilt in LUM-993 plan B
// Pass 6 to share the layout of the per-person page (me/MePermissionsView): one
// row per setting someone changed, with the connector in plain words
// (permissionGroups.groupFor) and a plain "Ask me first / Just do it" Select.
// Only per-user overrides exist on the server; everyone else uses the default.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { RowActions } from "../../components/page/RowActions";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { groupFor } from "../me/permissionGroups";

type Mode = "ASK" | "DO";

interface PermRow {
  user_id: string;
  email: string | null;
  connector: string;
  mode: Mode;
  is_default: boolean;
  updated_at: string | null;
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return "Request failed";
}

export function AdminConnectorPermissionsView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [localMode, setLocalMode] = useState<Record<string, Mode>>({});

  const listQ = useQuery({
    queryKey: ["admin", "permissions", "all"],
    queryFn: () => client.getJson<PermRow[]>("/api/v1/admin/permissions"),
  });

  const rowKey = (r: PermRow) => `${r.user_id}:${r.connector}`;

  const putM = useMutation({
    mutationFn: ({ user_id, connector, mode }: { user_id: string; connector: string; mode: Mode }) =>
      client.putJson<{ mode: Mode }, PermRow>(
        `/api/v1/admin/users/${encodeURIComponent(user_id)}/permissions/${encodeURIComponent(connector)}`,
        { mode },
      ),
    onSuccess: () => {
      toast("Saved.");
      void qc.invalidateQueries({ queryKey: ["admin", "permissions", "all"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  const delM = useMutation({
    mutationFn: ({ user_id, connector }: { user_id: string; connector: string }) =>
      client.delete<PermRow>(
        `/api/v1/admin/users/${encodeURIComponent(user_id)}/permissions/${encodeURIComponent(connector)}`,
      ),
    onSuccess: () => {
      toast("Back to the household default.");
      void qc.invalidateQueries({ queryKey: ["admin", "permissions", "all"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  if (listQ.isPending) {
    return (
      <LoadingPlaceholder label="Loading permissions…" className="max-w-2xl">
        <SkeletonText lines={4} />
      </LoadingPlaceholder>
    );
  }
  if (listQ.isError) {
    return (
      <ErrorState message="Lumogis couldn't load these settings." onRetry={() => void listQ.refetch()} />
    );
  }

  const rows = listQ.data ?? [];

  return (
    <section>
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        By default Lumogis asks before it acts for anyone. Settings that someone changed are listed here;
        everyone else is not.
      </p>

      {error ? (
        <Alert variant="destructive" className="mb-4 max-w-2xl">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <PageSection title="Changed settings" description="Choose what Lumogis does for each person, or put it back to the default.">
        {rows.length === 0 ? (
          <EmptyState
            title="Nothing changed yet"
            helperText="Everyone uses the default: Lumogis asks first. People change their own settings under What Lumogis may do."
          />
        ) : (
          <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
            {rows.map((r) => {
              const k = rowKey(r);
              const mode = localMode[k] ?? r.mode;
              const group = groupFor(r.connector);
              const who = r.email ?? r.user_id;
              const labels: Record<Mode, string> = { ASK: group.askLabel, DO: group.doLabel };
              return (
                <li key={k} className="flex min-h-12 flex-wrap items-center gap-x-3 gap-y-2 py-2">
                  <div className="min-w-0 flex-1 basis-48">
                    <div className="truncate text-sm font-medium">{who}</div>
                    <div className="text-xs text-muted-foreground">{group.title}</div>
                  </div>
                  <Select
                    value={mode}
                    onValueChange={(v) => setLocalMode((s) => ({ ...s, [k]: v as Mode }))}
                  >
                    <SelectTrigger aria-label={`Setting for ${who}: ${group.title}`} className="w-44">
                      <SelectValue>{(v: Mode | null) => labels[v ?? mode]}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="ASK">{labels.ASK}</SelectItem>
                      <SelectItem value="DO">{labels.DO}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    size="sm"
                    disabled={mode === r.mode || putM.isPending}
                    onClick={() => {
                      setError(null);
                      putM.mutate({ user_id: r.user_id, connector: r.connector, mode });
                    }}
                  >
                    Save
                  </Button>
                  <RowActions
                    label={`More actions for ${who}: ${group.title}`}
                    items={[
                      {
                        label: "Back to the default",
                        onSelect: () => {
                          setError(null);
                          setLocalMode((s) => {
                            const rest = { ...s };
                            delete rest[k];
                            return rest;
                          });
                          delM.mutate({ user_id: r.user_id, connector: r.connector });
                        },
                      },
                    ]}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </PageSection>
    </section>
  );
}
