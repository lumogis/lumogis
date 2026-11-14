// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-700: per-user timezone + quiet-hours window. The dispatcher (ADR 077)
// already gates every push-eligible notification — including the LUM-648
// email needs-you push — on `notification_user_settings.quiet_hours_*`; this
// editor is the missing write path, there was previously no way to set them.

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "../../api/client";
import {
  type NotificationPreferencesResponse,
  patchNotificationPreference,
} from "../../api/notificationPreferences";

const FALLBACK_ZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Moscow",
  "Africa/Cairo",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Pacific/Auckland",
];

function listTimezones(): string[] {
  try {
    const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] })
      .supportedValuesOf;
    if (supported) return supported("timeZone");
  } catch {
    // fall through to the curated list
  }
  return FALLBACK_ZONES;
}

// "22:00:00" (server) <-> "22:00" (<input type="time"> value).
function toInputTime(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 5);
}

interface Props {
  client: ApiClient;
  prefs: NotificationPreferencesResponse;
}

export function QuietHoursEditor({ client, prefs }: Props): JSX.Element {
  const queryClient = useQueryClient();
  const zones = listTimezones();

  const [timezone, setTimezone] = useState(prefs.timezone ?? "");
  const [start, setStart] = useState(toInputTime(prefs.quiet_hours_start));
  const [end, setEnd] = useState(toInputTime(prefs.quiet_hours_end));

  const mutation = useMutation({
    mutationKey: ["patch-notification-quiet-hours"],
    mutationFn: (
      patch: Partial<{
        timezone: string | null;
        quiet_hours_start: string | null;
        quiet_hours_end: string | null;
      }>,
    ) => patchNotificationPreference(client, patch),
    onSuccess: (resp) => {
      queryClient.setQueryData(["me", "notification-preferences"], resp);
    },
  });

  useEffect(() => {
    if (mutation.isPending) return;
    setTimezone(prefs.timezone ?? "");
    setStart(toInputTime(prefs.quiet_hours_start));
    setEnd(toInputTime(prefs.quiet_hours_end));
    // Only re-sync from the server value; re-running on mutation.isPending would
    // clobber in-progress edits the instant a save starts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs.timezone, prefs.quiet_hours_start, prefs.quiet_hours_end]);

  const hasWindow = Boolean(prefs.quiet_hours_start && prefs.quiet_hours_end);

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <h3 style={{ marginBottom: "0.5rem" }}>Quiet hours</h3>
      <p style={{ maxWidth: "42rem", opacity: 0.9, fontSize: "0.9rem" }}>
        While quiet hours are active, ntfy and Web Push are held back for everything except urgent
        security alerts — including the email &quot;needs you&quot; push. Nothing is lost: it still
        appears in-app right away and rolls into your next morning brief.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "flex-end", marginTop: "0.75rem" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.9rem" }}>
          Timezone
          <select
            aria-label="Quiet hours timezone"
            value={timezone}
            disabled={mutation.isPending}
            onChange={(e) => {
              const value = e.target.value;
              setTimezone(value);
              mutation.mutate({ timezone: value || null });
            }}
          >
            <option value="">Not set (defaults to UTC)</option>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.9rem" }}>
          Starts
          <input
            type="time"
            aria-label="Quiet hours start"
            value={start}
            disabled={mutation.isPending}
            onChange={(e) => setStart(e.target.value)}
            onBlur={() => {
              if (toInputTime(prefs.quiet_hours_start) === start) return;
              mutation.mutate({ quiet_hours_start: start || null });
            }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.9rem" }}>
          Ends
          <input
            type="time"
            aria-label="Quiet hours end"
            value={end}
            disabled={mutation.isPending}
            onChange={(e) => setEnd(e.target.value)}
            onBlur={() => {
              if (toInputTime(prefs.quiet_hours_end) === end) return;
              mutation.mutate({ quiet_hours_end: end || null });
            }}
          />
        </label>
        {hasWindow ? (
          <button
            type="button"
            disabled={mutation.isPending}
            onClick={() => {
              setStart("");
              setEnd("");
              mutation.mutate({ quiet_hours_start: null, quiet_hours_end: null });
            }}
          >
            Clear quiet hours
          </button>
        ) : null}
      </div>
      {mutation.isError ? (
        <p role="alert" style={{ color: "var(--warn, #b45309)", marginTop: "0.5rem" }}>
          Could not save quiet hours. Check the timezone and time values and try again.
        </p>
      ) : null}
    </div>
  );
}
