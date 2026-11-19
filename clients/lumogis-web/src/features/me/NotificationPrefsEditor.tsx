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
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { QuietHoursEditor } from "./QuietHoursEditor";

const TYPE_LABELS: Record<string, string> = {
  routine_elevation: "Routine elevation",
  signal_received: "Signal received",
  signal_digest: "Signal digest",
  action_executed: "Action executed",
  security_alert: "Security alert",
  consolidation_done: "Consolidation done",
  signal_needs_you: "Email needs you",
  signal_morning_brief: "Morning brief",
  // LUM-758. The prefs matrix lists every notification type for every user, so an
  // admin-only type still needs a label here or the raw slug renders.
  egress_allowlist_widened: "New mail host allowed",
};

// Plain names for the three places a notification can go. "Browser" is the browsers
// you enabled push in (see the "On this device" section); ntfy is the phone app.
const CHANNEL_LABELS: Record<ChannelId, string> = {
  ntfy: "ntfy",
  web_push: "Browser",
  in_app: "In Lumogis",
};

function typeLabel(raw: string): string {
  if (TYPE_LABELS[raw]) return TYPE_LABELS[raw];
  const spaced = raw.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// Developer detail for the page's collapsed "Details": which tier each type belongs to.
export function NotificationTierDetails({ prefs }: { prefs: NotificationPreferencesResponse }): JSX.Element {
  return (
    <div>
      <h3 className="m-0 mb-1 text-sm font-semibold">Tier of each notification type</h3>
      <ul role="list" className="m-0 list-none p-0 text-sm">
        {prefs.types.map((row) => (
          <li key={row.notification_type}>
            {typeLabel(row.notification_type)}: {row.tier}
          </li>
        ))}
      </ul>
    </div>
  );
}

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
    <>
      <PageSection
        title="What notifies you"
        description="For each kind of news, choose where Lumogis tells you: in a browser you enabled push in, on your phone with ntfy, or inside Lumogis."
      >
        <Table aria-label="Notification preference matrix">
          <TableHeader>
            <TableRow>
              <TableHead>News</TableHead>
              {channels.map((ch) => (
                <TableHead key={ch} className="text-center">
                  {CHANNEL_LABELS[ch]}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {prefs.types.map((row) => (
              <TableRow key={row.notification_type}>
                <TableCell className="whitespace-normal">
                  <div className="font-medium">{typeLabel(row.notification_type)}</div>
                  {PRODUCERLESS.has(row.notification_type) ? (
                    <div className="text-xs text-muted-foreground italic">Not available yet</div>
                  ) : null}
                </TableCell>
                {channels.map((ch) => {
                  const cell = row.channels.find((c) => c.channel === ch);
                  if (!cell) return <TableCell key={ch} />;
                  const disabled = !cell.mutable || mutation.isPending;
                  const label = `${typeLabel(row.notification_type)} — ${CHANNEL_LABELS[ch]}`;
                  return (
                    <TableCell key={ch} className="text-center">
                      <Checkbox
                        aria-label={label}
                        checked={cell.effective}
                        disabled={disabled}
                        title={
                          !cell.mutable
                            ? "This channel is not available for this kind of news."
                            : undefined
                        }
                        onCheckedChange={() => {
                          if (!cell.mutable) return;
                          mutation.mutate({
                            notification_type: row.notification_type as NotificationType,
                            channel: ch,
                            enabled: !cell.effective,
                          });
                        }}
                      />
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {mutation.isError ? (
          <Alert variant="destructive" className="mt-3">
            <AlertDescription>Could not save that choice. It was put back.</AlertDescription>
          </Alert>
        ) : null}
      </PageSection>
      <QuietHoursEditor client={client} prefs={prefs} />
    </>
  );
}
