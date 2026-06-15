// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { useMutation, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import {
  type ChannelId,
  type NotificationPreferencePatchItem,
  type NotificationPreferencesResponse,
  type NotificationType,
  patchNotificationPreference,
} from "../../api/notificationPreferences";

const TYPE_LABELS: Record<string, string> = {
  routine_elevation: "Routine elevation",
  signal_received: "Signal received",
  signal_digest: "Signal digest",
  action_executed: "Action executed",
  security_alert: "Security alert",
  consolidation_done: "Consolidation done",
};

const CHANNEL_LABELS: Record<ChannelId, string> = {
  ntfy: "ntfy",
  web_push: "Web Push",
  in_app: "In-app",
};

const PRODUCERLESS = new Set(["security_alert", "consolidation_done"]);

interface Props {
  client: ApiClient;
  prefs: NotificationPreferencesResponse;
}

export function NotificationPrefsEditor({ client, prefs }: Props): JSX.Element {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationKey: ["patch-notification-pref"],
    mutationFn: (item: NotificationPreferencePatchItem) =>
      patchNotificationPreference(client, { preferences: [item] }),
    onMutate: async (item) => {
      await queryClient.cancelQueries({ queryKey: ["me", "notification-preferences"] });
      const previous = queryClient.getQueryData<NotificationPreferencesResponse>([
        "me",
        "notification-preferences",
      ]);
      if (previous) {
        const next: NotificationPreferencesResponse = {
          ...previous,
          types: previous.types.map((row) => {
            if (row.notification_type !== item.notification_type) return row;
            return {
              ...row,
              channels: row.channels.map((cell) => {
                if (cell.channel !== item.channel) return cell;
                const enabled = item.enabled;
                return {
                  ...cell,
                  enabled,
                  effective: cell.mutable ? enabled : false,
                };
              }),
            };
          }),
        };
        queryClient.setQueryData(["me", "notification-preferences"], next);
      }
      return { previous };
    },
    onError: (_err, _item, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["me", "notification-preferences"], context.previous);
      }
    },
    onSettled: () => {
      if (
        !queryClient.isMutating({ mutationKey: ["patch-notification-pref"] })
      ) {
        void queryClient.invalidateQueries({ queryKey: ["me", "notification-preferences"] });
      }
    },
  });

  const channels: ChannelId[] = ["ntfy", "web_push", "in_app"];

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <h3 style={{ marginBottom: "0.5rem" }}>Notification preferences</h3>
      <p style={{ maxWidth: "42rem", opacity: 0.9, fontSize: "0.9rem" }}>
        Choose which channels receive each notification type. ntfy server credentials are configured
        under <a href="/me/connectors">Connectors</a>.
      </p>
      <div style={{ overflowX: "auto" }}>
        <table
          style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.9rem", marginTop: "0.75rem" }}
          aria-label="Notification preference matrix"
        >
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid rgba(128,128,128,0.35)" }}>
              <th style={{ padding: "0.5rem 0.35rem" }}>Type</th>
              {channels.map((ch) => (
                <th key={ch} style={{ padding: "0.5rem 0.35rem" }}>
                  {CHANNEL_LABELS[ch]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {prefs.types.map((row) => (
              <tr key={row.notification_type} style={{ verticalAlign: "top" }}>
                <td style={{ padding: "0.5rem 0.35rem" }}>
                  <div style={{ fontWeight: 600 }}>
                    {TYPE_LABELS[row.notification_type] ?? row.notification_type}
                  </div>
                  <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Tier: {row.tier}</div>
                  {PRODUCERLESS.has(row.notification_type) ? (
                    <div style={{ fontSize: "0.8rem", fontStyle: "italic" }}>Not available yet</div>
                  ) : null}
                </td>
                {channels.map((ch) => {
                  const cell = row.channels.find((c) => c.channel === ch);
                  if (!cell) return <td key={ch} />;
                  const disabled = !cell.mutable || mutation.isPending;
                  const label = `${TYPE_LABELS[row.notification_type] ?? row.notification_type} — ${CHANNEL_LABELS[ch]}`;
                  return (
                    <td key={ch} style={{ padding: "0.5rem 0.35rem", textAlign: "center" }}>
                      <input
                        type="checkbox"
                        aria-label={label}
                        checked={cell.effective}
                        disabled={disabled}
                        title={
                          !cell.mutable
                            ? "This channel is not available for this notification tier."
                            : undefined
                        }
                        onChange={() => {
                          if (!cell.mutable) return;
                          mutation.mutate({
                            notification_type: row.notification_type as NotificationType,
                            channel: ch,
                            enabled: !cell.effective,
                          });
                        }}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {mutation.isError ? (
        <p role="alert" style={{ color: "var(--warn, #b45309)", marginTop: "0.5rem" }}>
          Could not save preference. Changes were reverted.
        </p>
      ) : null}
    </div>
  );
}
