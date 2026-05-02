// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Phase 4C — enrol this browser for Web Push via user gesture only.
// Phase 4D — SW push + notificationclick in `src/pwa/sw.ts` + `src/pwa/swPush.ts` — see README.

import { useCallback, useMemo, useState, type CSSProperties } from "react";
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
import {
  createBrowserPushSubscription,
  isWebPushSupported,
  PushSubscribeError,
} from "../../pwa/webPushBrowser";

const VAPID_QK = ["notifications", "vapid-public-key"] as const;
const SUBS_QK = ["notifications", "web-push-subscriptions"] as const;

const tap: CSSProperties = {
  minHeight: "var(--lumogis-tap-target-min, 44px)",
  minWidth: "var(--lumogis-tap-target-min, 44px)",
};

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

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
        setActionMessage(
          result === "denied"
            ? "Notifications are blocked for this site. Allow them in the browser settings for this origin, then try again."
            : "Notifications were not allowed.",
        );
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

  if (!authed) {
    return (
      <section aria-labelledby="web-push-heading" style={{ marginTop: "var(--lumogis-space-5, 1.5rem)" }}>
        <h3 id="web-push-heading">Browser push (this device)</h3>
        <p style={{ maxWidth: "42rem" }}>Sign in to enrol this browser for Web Push.</p>
      </section>
    );
  }

  if (!supported) {
    return (
      <section aria-labelledby="web-push-heading" style={{ marginTop: "var(--lumogis-space-5, 1.5rem)" }}>
        <h3 id="web-push-heading">Browser push (this device)</h3>
        <p role="status" style={{ maxWidth: "42rem" }}>
          Browser push is not supported in this environment. Use a recent desktop or mobile browser over HTTPS (or
          localhost for development), with service workers and notifications enabled.
        </p>
      </section>
    );
  }

  const vapidUnavailable =
    vapidQ.isError && vapidQ.error instanceof ApiError && vapidQ.error.status === 503;

  const serverMsg = vapidQ.isError
    ? vapidQ.error instanceof ApiError
      ? formatWebPushUserMessage(vapidQ.error.status, vapidQ.error.detail)
      : describeApiError(vapidQ.error)
    : null;

  return (
    <section aria-labelledby="web-push-heading" style={{ marginTop: "var(--lumogis-space-5, 1.5rem)" }}>
      <h3 id="web-push-heading">Browser push (this device)</h3>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Registers this browser so Lumogis can send Web Push deliveries. After enrolment, the Lumogis service worker can show
        a system notification and open **/**, **`/chat`**, **`/approvals`**, or **`/me/notifications`** when you tap it
        — exact tray behaviour varies by OS and browser.
      </p>

      {vapidQ.isPending ? (
        <p aria-busy="true">Checking push configuration…</p>
      ) : vapidUnavailable ? (
        <p role="status" style={{ maxWidth: "42rem", color: "var(--lumogis-warn, #b76e00)" }}>
          Web Push is not configured on this Lumogis server. Ask whoever runs Lumogis to set the VAPID keys for the
          orchestrator.
        </p>
      ) : vapidQ.isError ? (
        <p role="alert">{serverMsg ?? "Could not load push configuration."}</p>
      ) : null}

      {notifPermission === "denied" && !vapidUnavailable && !vapidQ.isPending && (
        <p role="status" style={{ maxWidth: "42rem" }}>
          Notifications were blocked for this origin. Adjust the permission in your browser&apos;s site settings, then
          reload this page if you change it.
        </p>
      )}

      {!vapidUnavailable && !vapidQ.isPending && vapidQ.data && notifPermission !== "denied" ? (
        <div style={{ marginTop: "0.75rem" }}>
          <button
            type="button"
            className="lumogis-push-opt-in__enable"
            onClick={() => void onEnableBrowserPush()}
            disabled={
              busy ||
              patchMut.isPending ||
              deleteMut.isPending
            }
            style={tap}
          >
            {busy ? "Working…" : "Enable browser push on this device"}
          </button>
        </div>
      ) : null}

      {actionMessage ? (
        <p role="status" style={{ marginTop: "0.75rem", maxWidth: "42rem" }}>
          {actionMessage}
        </p>
      ) : null}

      {subsQ.isError ? (
        <p role="alert" style={{ marginTop: "0.75rem" }}>
          {subsQ.error instanceof ApiError ? formatWebPushUserMessage(subsQ.error.status, subsQ.error.detail) : "Could not load subscriptions."}
        </p>
      ) : subsQ.isPending && !vapidUnavailable ? (
        <p aria-busy="true" style={{ marginTop: "0.75rem" }}>
          Loading subscriptions…
        </p>
      ) : subsQ.data ? (
        subsQ.data.subscriptions.length > 0 ? (
          <WebPushSubscriptionList
            rows={subsQ.data.subscriptions}
            onPatch={(id, patch) => patchMut.mutate({ id, patch })}
            onDelete={(id) => deleteMut.mutate(id)}
            patchPending={patchMut.isPending}
            deletePending={deleteMut.isPending}
          />
        ) : (
          <p style={{ marginTop: "1rem", opacity: 0.9 }} role="status">
            No browsers are registered for Web Push on this account yet. Use Enable browser push after granting
            notification permission for this origin.
          </p>
        )
      ) : null}
    </section>
  );
}

function WebPushSubscriptionList(props: {
  rows: WebPushSubscriptionRedacted[];
  onPatch: (id: number, patch: { notify_on_signals?: boolean; notify_on_shared_scope?: boolean }) => void;
  onDelete: (id: number) => void;
  patchPending: boolean;
  deletePending: boolean;
}): JSX.Element {
  const { rows, onPatch, onDelete, patchPending, deletePending } = props;
  return (
    <div style={{ marginTop: "1rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
      <h4 style={{ fontSize: "1rem", margin: 0 }}>Registered browsers</h4>
      {rows.map((row) => (
        <article
          key={row.id}
          style={{
            border: "1px solid var(--lumogis-border, #d6dae4)",
            borderRadius: "var(--lumogis-radius-md, 8px)",
            padding: "0.85rem",
            background: "var(--lumogis-surface, #fff)",
            maxWidth: "42rem",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: "0.35rem", wordBreak: "break-word" }}>
            {row.endpoint_origin}
          </div>
          <dl
            style={{
              margin: 0,
              display: "grid",
              gap: "0.25rem",
              fontSize: "0.9rem",
              opacity: 0.95,
            }}
          >
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem 1rem" }}>
              <dt style={{ opacity: 0.75 }}>Created</dt>
              <dd style={{ margin: 0 }}>{formatWhen(row.created_at)}</dd>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem 1rem" }}>
              <dt style={{ opacity: 0.75 }}>Last seen</dt>
              <dd style={{ margin: 0 }}>{formatWhen(row.last_seen_at)}</dd>
            </div>
            {row.last_error ? (
              <div>
                <dt style={{ opacity: 0.75 }}>Last delivery note</dt>
                <dd style={{ margin: 0 }}>{row.last_error}</dd>
              </div>
            ) : null}
            {row.user_agent ? (
              <div style={{ wordBreak: "break-word" }}>
                <dt style={{ opacity: 0.75 }}>User-Agent</dt>
                <dd style={{ margin: 0 }}>{row.user_agent}</dd>
              </div>
            ) : null}
          </dl>
          <div style={{ marginTop: "0.75rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            <label
              style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start", minHeight: tap.minHeight }}
            >
              <input
                type="checkbox"
                checked={row.notify_on_signals}
                disabled={patchPending || deletePending}
                onChange={(e) =>
                  onPatch(row.id, { notify_on_signals: e.target.checked })
                }
                aria-label={`Notify on signals for ${row.endpoint_origin}`}
              />
              <span>Notify on signals (future signal pushes)</span>
            </label>
            <label
              style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start", minHeight: tap.minHeight }}
            >
              <input
                type="checkbox"
                checked={row.notify_on_shared_scope}
                disabled={patchPending || deletePending}
                onChange={(e) =>
                  onPatch(row.id, { notify_on_shared_scope: e.target.checked })
                }
                aria-label={`Notify on shared scope for ${row.endpoint_origin}`}
              />
              <span>Notify on shared-scope events</span>
            </label>
          </div>
          <button
            type="button"
            style={{ ...tap, marginTop: "0.75rem", borderRadius: "var(--lumogis-radius-md, 8px)" }}
            disabled={patchPending || deletePending}
            onClick={() => {
              if (globalThis.confirm("Remove this browser from Web Push for your account?")) onDelete(row.id);
            }}
          >
            {deletePending ? "Removing…" : "Remove this browser"}
          </button>
        </article>
      ))}
    </div>
  );
}
