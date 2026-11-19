// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin → System mail (LUM-65). A dedicated page rather than a row in
// Connector credentials because the Send test is the point: this is the only
// credential in Lumogis whose configuration is not believed until it has
// demonstrably delivered a message, and that verb has nowhere to live in a
// generic key/value credential form.

import { ChevronDown } from "lucide-react";
import { useEffect, useState, type FormEvent, type JSX } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "../../api/client";
import {
  deleteSystemMail,
  getSystemMail,
  putSystemMail,
  testSystemMail,
  type SystemMailPublic,
} from "../../api/systemMail";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
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
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { formatDateTime } from "../../util/formatTimestamp";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
  if (e instanceof Error) return e.message;
  return "Request failed";
}

export function AdminSystemMailView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin", "system-mail"],
    queryFn: () => getSystemMail(client),
  });

  const [host, setHost] = useState("");
  const [port, setPort] = useState(587);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fromAddr, setFromAddr] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  useEffect(() => {
    const d = q.data;
    if (!d) return;
    setHost(d.host ?? "");
    setPort(d.port ?? 587);
    setUsername(d.username ?? "");
    setFromAddr(d.from_addr ?? "");
    // Never seeded from the server — it is never sent to us. Blank means
    // "keep the stored one".
    setPassword("");
  }, [q.data]);

  const save = useMutation({
    mutationFn: () =>
      putSystemMail(client, {
        host: host.trim(),
        port: Number(port),
        username: username.trim(),
        // Omitted entirely when blank: an explicit "" is a 422 server-side,
        // precisely so a round-tripped redacted form cannot blank the secret.
        ...(password ? { password } : {}),
        from_addr: fromAddr.trim(),
      }),
    onSuccess: (data) => {
      qc.setQueryData(["admin", "system-mail"], data);
      setPassword("");
      setError(null);
      toast(
        data.email_reset_enabled
          ? "Saved."
          : "Saved. Send a test message to turn on email password reset.",
      );
    },
    onError: (e) => setError(errMsg(e)),
  });

  const test = useMutation({
    mutationFn: () => testSystemMail(client),
    onSuccess: async () => {
      setError(null);
      toast("Test message sent. Check the inbox for your admin account.");
      await qc.invalidateQueries({ queryKey: ["admin", "system-mail"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  const remove = useMutation({
    mutationFn: () => deleteSystemMail(client),
    onSuccess: async () => {
      setError(null);
      toast("Email settings removed. Email password reset is off.");
      await qc.invalidateQueries({ queryKey: ["admin", "system-mail"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  if (q.isLoading) {
    return (
      <LoadingPlaceholder label="Loading email settings…" className="max-w-2xl">
        <SkeletonText lines={5} />
      </LoadingPlaceholder>
    );
  }
  if (q.isError || !q.data) {
    return <ErrorState message="Lumogis couldn't load these email settings." onRetry={() => void q.refetch()} />;
  }

  const data = q.data as SystemMailPublic;
  const busy = save.isPending || test.isPending || remove.isPending;

  function onSubmit(e: FormEvent): void {
    e.preventDefault();
    if (!busy) save.mutate();
  }

  return (
    <section data-testid="admin-system-mail" className="flex flex-col gap-2">
      <p className="m-0 max-w-2xl text-sm text-muted-foreground">
        The mailbox Lumogis sends from as itself — password-reset links today. This is
        not a household member’s mailbox: using one would be circular, since the
        account most likely to need a reset email is the one whose credentials would
        be needed to send it.
      </p>

      <EnablementSummary data={data} />

      {error !== null ? (
        <Alert variant="destructive" className="max-w-2xl">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <PageSection title="Sending account" description="The mail server Lumogis signs in to.">
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="system-mail-host">Mail server</Label>
            <Input
              id="system-mail-host"
              required
              value={host}
              onChange={(e) => setHost(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="system-mail-port">Port</Label>
            <Input
              id="system-mail-port"
              type="number"
              min={1}
              max={65535}
              required
              value={port}
              onChange={(e) => setPort(Number(e.target.value))}
              disabled={busy}
              className="max-w-32"
            />
            <p className="m-0 text-xs text-muted-foreground">
              Port 465 uses implicit TLS; anything else uses STARTTLS.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="system-mail-username">Username</Label>
            <Input
              id="system-mail-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="system-mail-password">Password</Label>
            <Input
              id="system-mail-password"
              type="password"
              autoComplete="new-password"
              placeholder={data.password_set ? "•••••••• (leave blank to keep)" : ""}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="system-mail-from">From address</Label>
            <Input
              id="system-mail-from"
              type="email"
              required
              value={fromAddr}
              onChange={(e) => setFromAddr(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={busy} data-testid="system-mail-save">
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => test.mutate()}
              disabled={busy || !data.configured}
              data-testid="system-mail-test"
            >
              {test.isPending ? "Sending…" : "Send test"}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => setConfirmRemove(true)}
              disabled={busy || !data.configured}
              data-testid="system-mail-delete"
            >
              Remove
            </Button>
          </div>
        </form>
      </PageSection>

      <AlertDialog open={confirmRemove} onOpenChange={setConfirmRemove}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove these email settings?</AlertDialogTitle>
            <AlertDialogDescription>
              Lumogis forgets the mail server and password, and email password reset turns off until you
              set them up and send a test message again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                setConfirmRemove(false);
                remove.mutate();
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Collapsible className="max-w-2xl">
        <CollapsibleTrigger
          render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
        >
          <ChevronDown aria-hidden="true" className="transition-transform group-aria-expanded/trigger:rotate-180" />
          Details
        </CollapsibleTrigger>
        <CollapsibleContent keepMounted className="flex flex-col gap-2 pt-2 text-sm text-muted-foreground">
          <p className="m-0">
            For whoever runs the server. Reset links are built from the public origin only, so it must be set
            as <code>LUMOGIS_PUBLIC_ORIGIN</code>.
          </p>
          <p className="m-0">
            The public request page needs the verification key <code>LUMOGIS_ALTCHA_HMAC_KEY</code>.
          </p>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}

/**
 * Enablement is a conjunction, so an operator staring at a disabled feature
 * needs to see which conjunct is missing. The public status endpoint refuses to
 * say — that is deployment information, and this is where it belongs.
 * Env var names live in the page's collapsed Details, not in these hints.
 */
function EnablementSummary({ data }: { data: SystemMailPublic }): JSX.Element {
  const rows: { label: string; ok: boolean; hint: string }[] = [
    {
      label: "Mail server set up",
      ok: data.configured,
      hint: "Fill in the form below and save.",
    },
    {
      label: "Test message delivered",
      ok: Boolean(data.last_test_ok_at),
      hint: "Use Send test. Changing the server, port, username, password or From address clears this.",
    },
    {
      label: "Public address set",
      ok: data.public_origin_set,
      hint: "The server needs to know its public address, because reset links are built from it (see Details).",
    },
    {
      label: "Verification key set",
      ok: data.altcha_hmac_set,
      hint: "The public request page needs a verification key on the server (see Details).",
    },
  ];

  return (
    <PageSection
      title="Email password reset"
      description={
        <>
          <Badge variant={data.email_reset_enabled ? "default" : "outline"}>
            {data.email_reset_enabled ? "On" : "Not on yet"}
          </Badge>
          {data.last_test_ok_at !== null ? (
            <> Last successful test {formatDateTime(data.last_test_ok_at)}</>
          ) : null}
        </>
      }
    >
      <ul role="list" data-testid="system-mail-enablement" className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
        <li className="sr-only">
          Email password reset: {data.email_reset_enabled ? "enabled" : "not enabled"}
        </li>
        {rows.map((r) => (
          <li key={r.label}>
            <span aria-hidden="true">{r.ok ? "✓" : "✗"}</span>{" "}
            <span className="sr-only">{r.ok ? "Done: " : "Missing: "}</span>
            {r.label}
            {!r.ok && <span className="text-muted-foreground"> — {r.hint}</span>}
          </li>
        ))}
      </ul>
    </PageSection>
  );
}
