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
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";

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

// The Select needs a non-empty value for "Not set"; it maps back to null on save.
const NO_ZONE = "__none__";

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
    <PageSection
      title="Quiet hours"
      description={
        <>
          While quiet hours are active, ntfy and browser push are held back for everything except urgent
          security alerts, including the email &quot;needs you&quot; push. Nothing is lost: it still
          appears in Lumogis right away and rolls into your next morning brief.
        </>
      }
    >
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="quiet-hours-timezone">Timezone</Label>
          <Select
            value={timezone === "" ? NO_ZONE : timezone}
            disabled={mutation.isPending}
            onValueChange={(v) => {
              const value = v === NO_ZONE || v == null ? "" : String(v);
              setTimezone(value);
              mutation.mutate({ timezone: value || null });
            }}
          >
            <SelectTrigger id="quiet-hours-timezone" aria-label="Timezone" className="min-w-56">
              <SelectValue>{(v: string | null) => (v == null || v === NO_ZONE ? "Not set (UTC)" : v)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_ZONE}>Not set (UTC)</SelectItem>
              {zones.map((z) => (
                <SelectItem key={z} value={z}>
                  {z}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="quiet-hours-start">From</Label>
          <Input
            id="quiet-hours-start"
            type="time"
            value={start}
            disabled={mutation.isPending}
            onChange={(e) => setStart(e.target.value)}
            onBlur={() => {
              if (toInputTime(prefs.quiet_hours_start) === start) return;
              mutation.mutate({ quiet_hours_start: start || null });
            }}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="quiet-hours-end">Until</Label>
          <Input
            id="quiet-hours-end"
            type="time"
            value={end}
            disabled={mutation.isPending}
            onChange={(e) => setEnd(e.target.value)}
            onBlur={() => {
              if (toInputTime(prefs.quiet_hours_end) === end) return;
              mutation.mutate({ quiet_hours_end: end || null });
            }}
          />
        </div>
        {hasWindow ? (
          <Button
            type="button"
            variant="outline"
            disabled={mutation.isPending}
            onClick={() => {
              setStart("");
              setEnd("");
              mutation.mutate({ quiet_hours_start: null, quiet_hours_end: null });
            }}
          >
            Clear quiet hours
          </Button>
        ) : null}
      </div>
      {mutation.isError ? (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>
            Could not save quiet hours. Check the timezone and times and try again.
          </AlertDescription>
        </Alert>
      ) : null}
    </PageSection>
  );
}
