// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 4C — enrol this browser for Web Push via user gesture only.
// Phase 4D — SW push + notificationclick in `src/pwa/sw.ts` + `src/pwa/swPush.ts` — see README.

import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteWebPushSubscription,
  describeApiError,
  formatWebPushUserMessage,
  getVapidPublicKey,
  listWebPushSubscriptions,
  patchWebPushSubscription,
  subscribeWebPush,
  type WebPushSubscriptionRedacted,
} from "../../api/webPush";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
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
import { Checkbox } from "../../components/ui/checkbox";
import { Label } from "../../components/ui/label";
import { formatDateTime } from "../../util/formatTimestamp";
import {
  createBrowserPushSubscription,
  isWebPushSupported,
  PushSubscribeError,
} from "../../pwa/webPushBrowser";

const VAPID_QK = ["notifications", "vapid-public-key"] as const;
const SUBS_QK = ["notifications", "web-push-subscriptions"] as const;

export function PushOptIn(): JSX.Element {
  const { client, status } = useAuth();
  const qc = useQueryClient();
  const supported = useMemo(() => isWebPushSupported(), []);

  const [busy, setBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [notifPermission, setNotifPermission] = useState<NotificationPermission>(() =>
    typeof Notification !== "undefined" ? Notification.permission : "denied",
  );

  const authed = status === "authenticated";

  const vapidQ = useQuery({
    queryKey: VAPID_QK,
    queryFn: () => getVapidPublicKey(client),
    enabled: authed && supported,
    retry: false,
  });

  const subsQ = useQuery({
    queryKey: SUBS_QK,
    queryFn: () => listWebPushSubscriptions(client),
    enabled: authed && supported,
    retry: false,
  });

  const patchMut = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: number;
      patch: { notify_on_signals?: boolean; notify_on_shared_scope?: boolean };
    }) => patchWebPushSubscription(client, id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: SUBS_QK }),
    onError: (e: unknown) => setActionMessage(describeApiError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteWebPushSubscription(client, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: SUBS_QK }),
    onError: (e: unknown) => setActionMessage(describeApiError(e)),
  });

  const onEnableBrowserPush = useCallback(async () => {
    if (!supported || busy) return;
    setBusy(true);
    setActionMessage(null);
    try {
      let vapidResp: Awaited<ReturnType<typeof getVapidPublicKey>>;
      try {
        vapidResp = await getVapidPublicKey(client);
      } catch (e) {
        if (e instanceof ApiError) {
          setActionMessage(formatWebPushUserMessage(e.status, e.detail));
          return;
        }
        throw e;
      }

      const result = await Notification.requestPermission();
      setNotifPermission(result);
      if (result !== "granted") {
        // A refusal is explained by the standing "blocked" message below (notifPermission),
        // so only the dismissed-without-choosing case needs its own line.
        if (result !== "denied") setActionMessage("Notifications were not allowed.");
        return;
      }

      const sub = await createBrowserPushSubscription(vapidResp.public_key);
      const j = sub.toJSON() as { endpoint?: string; keys?: { p256dh?: string; auth?: string } };
      const endpoint = j.endpoint;
      const k = j.keys;
      if (
        typeof endpoint !== "string" ||
        typeof k?.p256dh !== "string" ||
        typeof k?.auth !== "string"
      ) {
        throw new PushSubscribeError("Could not read subscription keys from this browser.");
      }

      await subscribeWebPush(client, {
        endpoint,
        keys: { p256dh: k.p256dh, auth: k.auth },
        user_agent: typeof navigator !== "undefined" ? navigator.userAgent?.slice(0, 256) : undefined,
      });
      await qc.invalidateQueries({ queryKey: SUBS_QK });
      setActionMessage("This browser is registered for Web Push.");
    } catch (e: unknown) {
      if (e instanceof PushSubscribeError) setActionMessage(e.message);
      else setActionMessage(describeApiError(e));
    } finally {
      setBusy(false);
    }
  }, [busy, client, qc, supported]);

  const title = "On this device";

  if (!authed) {
    return (
      <PageSection title={title}>
        <p className="m-0 text-sm text-muted-foreground">Sign in to turn on notifications in this browser.</p>
      </PageSection>
    );
  }

  if (!supported) {
    return (
      <PageSection title={title}>
        <p role="status" className="m-0 text-sm text-muted-foreground">
          This browser can't show notifications from Lumogis. Use a recent desktop or phone browser over a secure
          (https) connection, with notifications turned on.
        </p>
      </PageSection>
    );
  }

  const vapidUnavailable =
    vapidQ.isError && vapidQ.error instanceof ApiError && vapidQ.error.status === 503;

  const serverMsg = vapidQ.isError
    ? vapidQ.error instanceof ApiError
      ? formatWebPushUserMessage(vapidQ.error.status, vapidQ.error.detail)
      : describeApiError(vapidQ.error)
    : null;

  const rows = subsQ.data?.subscriptions ?? [];
  const pending = patchMut.isPending || deleteMut.isPending;

  return (
    <PageSection
      title={title}
      description="Turn this on to get a notification in this browser when something needs you. Tapping one opens Lumogis on the right page; how it looks depends on your device."
    >
      {vapidQ.isPending ? (
        <p aria-busy="true" className="m-0 text-sm text-muted-foreground">
          Checking whether notifications are set up…
        </p>
      ) : vapidUnavailable ? (
        <p role="status" className="m-0 text-sm text-muted-foreground">
          Browser notifications are not set up on this Lumogis yet. Ask whoever runs Lumogis to turn them on.
        </p>
      ) : vapidQ.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{serverMsg ?? "Could not check whether notifications are set up."}</AlertDescription>
        </Alert>
      ) : null}

      {notifPermission === "denied" && !vapidUnavailable && !vapidQ.isPending && (
        <p role="status" className="m-0 text-sm">
          Notifications are blocked for this site in your browser settings. Allow them there, then reload this page.
        </p>
      )}

      {!vapidUnavailable && !vapidQ.isPending && vapidQ.data && notifPermission !== "denied" ? (
        <div className="mt-3">
          <Button type="button" onClick={() => void onEnableBrowserPush()} disabled={busy || pending}>
            {busy ? "Working…" : "Enable browser push on this device"}
          </Button>
        </div>
      ) : null}

      {actionMessage ? (
        <p role="status" className="mt-3 mb-0 text-sm">
          {actionMessage}
        </p>
      ) : null}

      {subsQ.isError ? (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>
            {subsQ.error instanceof ApiError
              ? formatWebPushUserMessage(subsQ.error.status, subsQ.error.detail)
              : "Could not load your browsers."}
          </AlertDescription>
        </Alert>
      ) : subsQ.isPending && !vapidUnavailable ? (
        <p aria-busy="true" className="mt-3 mb-0 text-sm text-muted-foreground">
          Loading your browsers…
        </p>
      ) : subsQ.data ? (
        rows.length > 0 ? (
          <WebPushSubscriptionList
            rows={rows}
            onPatch={(id, patch) => patchMut.mutate({ id, patch })}
            onDelete={(id) => deleteMut.mutate(id)}
            pending={pending}
          />
        ) : (
          <p role="status" className="mt-3 mb-0 text-sm text-muted-foreground">
            No browser is signed up for notifications yet. Use the button above to allow them in this one.
          </p>
        )
      ) : null}
    </PageSection>
  );
}

function WebPushSubscriptionList(props: {
  rows: WebPushSubscriptionRedacted[];
  onPatch: (id: number, patch: { notify_on_signals?: boolean; notify_on_shared_scope?: boolean }) => void;
  onDelete: (id: number) => void;
  pending: boolean;
}): JSX.Element {
  const { rows, onPatch, onDelete, pending } = props;
  const [removing, setRemoving] = useState<WebPushSubscriptionRedacted | null>(null);
  return (
    <div className="mt-4">
      <h3 className="m-0 text-sm font-semibold">Browsers signed up</h3>
      <ul role="list" className="m-0 mt-1 flex list-none flex-col divide-y divide-border p-0">
        {rows.map((row) => (
          <li key={row.id} role="group" aria-label={row.endpoint_origin} className="flex flex-col gap-2 py-3">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium break-words">{row.endpoint_origin}</div>
                <div className="text-xs text-muted-foreground">
                  Added {formatDateTime(row.created_at)}. Last used {formatDateTime(row.last_seen_at)}.
                </div>
                {row.last_error ? (
                  <div className="text-xs text-muted-foreground">Last problem: {row.last_error}</div>
                ) : null}
                {row.user_agent ? (
                  <div className="text-xs break-words text-muted-foreground">{row.user_agent}</div>
                ) : null}
              </div>
              <RowActions
                label={`Actions for ${row.endpoint_origin}`}
                items={[
                  { label: "Remove this browser", destructive: true, disabled: pending, onSelect: () => setRemoving(row) },
                ]}
              />
            </div>
            <div className="flex items-start gap-2">
              <Checkbox
                id={`push-signals-${row.id}`}
                checked={row.notify_on_signals}
                disabled={pending}
                onCheckedChange={(checked) => onPatch(row.id, { notify_on_signals: checked === true })}
              />
              <Label htmlFor={`push-signals-${row.id}`} className="font-normal">
                Tell me about emails that need me (the sender and subject are sent to this device)
              </Label>
            </div>
            <div className="flex items-start gap-2">
              <Checkbox
                id={`push-shared-${row.id}`}
                checked={row.notify_on_shared_scope}
                disabled={pending}
                onCheckedChange={(checked) => onPatch(row.id, { notify_on_shared_scope: checked === true })}
              />
              <Label htmlFor={`push-shared-${row.id}`} className="font-normal">
                Tell me when something is shared with the household
              </Label>
            </div>
          </li>
        ))}
      </ul>

      <AlertDialog open={removing !== null} onOpenChange={(open) => (open ? undefined : setRemoving(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this browser?</AlertDialogTitle>
            <AlertDialogDescription>
              {removing?.endpoint_origin} will stop getting notifications from Lumogis. You can turn them on again
              from that browser later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructiveSolid"
              onClick={() => {
                if (removing) onDelete(removing.id);
                setRemoving(null);
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
