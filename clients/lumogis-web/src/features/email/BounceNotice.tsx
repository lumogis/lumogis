// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "This message did not arrive" (LUM-820).
//
// The half of bounce handling the user can actually see. Everything before it
// stopped Lumogis LOSING the evidence — parsing the report, tying it to the
// send, keeping it out of the triage lanes. None of that stops someone
// believing a message arrived when it did not. Only this does.
//
// Three decisions worth keeping:
//
//  * **Permanent bounces only.** The backend filters 4.x.x, which is still
//    being retried. A notice about a message the server has not given up on
//    trains people to ignore the notice that matters.
//  * **Dismissible, per bounce.** A banner that cannot be dismissed becomes
//    wallpaper, and the next real failure arrives into something nobody reads.
//  * **Named by message, not by identifier.** "Lunch? did not reach
//    jhon@example.com" is actionable; a Message-ID is not. When the send row
//    has been pruned the recipient alone still carries the point.

import { useCallback, useEffect, useState } from "react";
import type { JSX } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import type { EmailBounce } from "../../api/emailBounces";

export interface BounceNoticeProps {
  /** Injected, like ComposerApi, so this renders without a live client. */
  fetchBounces: () => Promise<EmailBounce[]>;
  acknowledge: (id: string) => Promise<void>;
}

export function BounceNotice(props: BounceNoticeProps): JSX.Element | null {
  const { fetchBounces, acknowledge } = props;
  const [bounces, setBounces] = useState<EmailBounce[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchBounces()
      .then((rows) => {
        if (!cancelled) setBounces(rows);
      })
      // Silent: a failed lookup must not put an error banner over the inbox.
      // The cost is a missed notice, which is the status quo before LUM-820.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [fetchBounces]);

  const dismiss = useCallback(
    (id: string) => {
      // Optimistic: the row leaves immediately. A failed acknowledge means it
      // returns on the next load, which is the right direction to be wrong in
      // — far better than a notice that looks dismissed but is not recorded.
      setBounces((current) => current.filter((b) => b.id !== id));
      acknowledge(id).catch(() => {});
    },
    [acknowledge],
  );

  if (bounces.length === 0) return null;

  return (
    <Alert variant="destructive" aria-label="Undelivered messages" className="mb-4">
      <AlertTitle>
        {bounces.length === 1
          ? "A message you sent did not arrive"
          : `${bounces.length} messages you sent did not arrive`}
      </AlertTitle>
      <AlertDescription>
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {bounces.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>
                {b.subject ? <strong>{b.subject}</strong> : <em>a message</em>} did not reach{" "}
                <span className="break-all font-medium">{b.recipient}</span>
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => dismiss(b.id)}
                aria-label={`Dismiss notice for ${b.recipient}`}
              >
                Dismiss
              </Button>
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
