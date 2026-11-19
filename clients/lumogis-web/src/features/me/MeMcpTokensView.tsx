// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Connected AI apps: keys that let an AI app (Claude Desktop and the like) read, and
// optionally write, your Lumogis. Rebuilt on the shared components in LUM-993 plan B
// Pass 5: "Create a key", the key shown once in a Dialog, Revoke confirmed.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { ApiError } from "../../api/client";
import type { McpTokenRow } from "../../api/mcpTokens";
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
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { formatDateTime } from "../../util/formatTimestamp";
import { CopyOnceModal } from "../_shared/CopyOnceModal";
import { accessLabel } from "./mcpTokenDisplay";

interface MintRes {
  plaintext: string;
  token: McpTokenRow;
}

export function MeMcpTokensView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [writable, setWritable] = useState(false); // LUM-527: default least-privilege
  const [err, setErr] = useState<string | null>(null);
  const [plain, setPlain] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [revoking, setRevoking] = useState<McpTokenRow | null>(null);

  const listQ = useQuery({
    queryKey: ["mcp", "me"],
    queryFn: () => client.getJson<McpTokenRow[]>("/api/v1/me/mcp-tokens"),
  });

  const mintM = useMutation({
    mutationFn: () =>
      client.postJson<{ label: string; scopes: string[] }, MintRes>("/api/v1/me/mcp-tokens", {
        label: label.trim(),
        scopes: writable ? ["mcp:read", "mcp:write"] : ["mcp:read"],
      }),
    onSuccess: (data) => {
      setPlain(data.plaintext);
      setShowModal(true);
      setLabel("");
      setWritable(false); // back to least-privilege default for the next mint
      void qc.invalidateQueries({ queryKey: ["mcp", "me"] });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.detail : "Could not create the key. Try again.");
    },
  });

  const delM = useMutation({
    mutationFn: (id: string) => client.delete(`/api/v1/me/mcp-tokens/${encodeURIComponent(id)}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["mcp", "me"] });
      toast("Key revoked.");
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.detail : "Could not revoke the key. Try again.");
    },
  });

  if (listQ.isPending) {
    return (
      <LoadingPlaceholder label="Loading your keys…" className="max-w-2xl">
        <SkeletonText lines={4} />
      </LoadingPlaceholder>
    );
  }
  if (listQ.isError) {
    return <ErrorState message="Lumogis couldn't load your keys." onRetry={() => void listQ.refetch()} />;
  }

  const keys = listQ.data ?? [];

  const create = (e: FormEvent) => {
    e.preventDefault();
    if (!label.trim()) return;
    setErr(null);
    mintM.mutate();
  };

  return (
    <div className="flex flex-col gap-2">
      {err ? (
        <Alert variant="destructive" className="max-w-2xl">
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      ) : null}

      <PageSection
        title="Your keys"
        description="An AI app needs a key to reach your Lumogis. Each key can be taken away at any time."
      >
        {keys.length === 0 ? (
          <EmptyState title="No keys yet" helperText="Create a key below, then paste it into the AI app you want to connect." />
        ) : (
          <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
            {keys.map((t) => (
              <li key={t.id} className="flex min-h-12 flex-wrap items-center gap-2 py-1.5">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium break-words">{t.label}</div>
                  <div className="text-xs text-muted-foreground">
                    Created {formatDateTime(t.created_at)}. {accessLabel(t.scopes)}.
                  </div>
                </div>
                <RowActions
                  label={`Actions for ${t.label}`}
                  items={[{ label: "Revoke", destructive: true, onSelect: () => setRevoking(t) }]}
                />
              </li>
            ))}
          </ul>
        )}
      </PageSection>

      <PageSection title="Create a key" description="Name it after the app that will use it, so you can tell your keys apart.">
        <form className="flex flex-col gap-4" onSubmit={create}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-key-name">Key name</Label>
            <Input
              id="new-key-name"
              placeholder="For example, Claude Desktop"
              value={label}
              maxLength={64}
              onChange={(e) => setLabel(e.target.value)}
            />
          </div>
          <RadioGroup
            aria-label="What the app may do"
            value={writable ? "write" : "read"}
            onValueChange={(v) => setWritable(v === "write")}
          >
            <div className="flex items-center gap-2">
              <RadioGroupItem id="key-access-read" value="read" />
              <Label htmlFor="key-access-read" className="font-normal">
                Read-only
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem id="key-access-write" value="write" />
              <Label htmlFor="key-access-write" className="font-normal">
                Read + write
              </Label>
            </div>
          </RadioGroup>
          <div>
            <Button type="submit" disabled={!label.trim() || mintM.isPending}>
              Create a key
            </Button>
          </div>
        </form>
      </PageSection>

      {plain !== null && (
        <CopyOnceModal
          open={showModal}
          title="Your new key"
          plaintext={plain}
          onClose={() => {
            setShowModal(false);
            setPlain(null);
          }}
        />
      )}

      <AlertDialog open={revoking !== null} onOpenChange={(open) => (open ? undefined : setRevoking(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke {revoking?.label}?</AlertDialogTitle>
            <AlertDialogDescription>
              The app using this key loses access to Lumogis straight away. You can create a new key later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                if (revoking) delM.mutate(revoking.id);
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
