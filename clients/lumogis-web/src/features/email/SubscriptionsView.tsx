// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-697 — the sender-grouped Subscriptions surface.
//
// Deliberately a separate view from the cleanup pass. Cleanup groups by
// CATEGORY ("Health: 41 messages → move to Health"); this groups by SENDER
// ("news@retailer.example: 41 messages → unsubscribe"). Those are different
// questions and do not belong in one list.
//
// Three affordances per sender, in decreasing order of how much they depend on
// the sender cooperating:
//   1. Unsubscribe (mailto:) — batched into ONE proposal, reviewed once.
//   2. Unsubscribe (https:)  — opens in the USER'S BROWSER. We never fetch it.
//   3. Hide                  — works whether or not the sender cooperates.

import { useCallback, useEffect, useMemo, useState } from "react";

import type { ApiClient } from "../../api/client";
import {
  clearSenderRule,
  fetchSubscriptions,
  fetchUnsubscribeJobs,
  isSafeUnsubscribeUrl,
  isUnsubscribeJobActive,
  proposeUnsubscribe,
  setSenderRule,
  type SubscriptionRow,
} from "../../api/emailUnsubscribe";

// Mirrors MAX_UNSUBSCRIBE_BATCH in orchestrator/actions/handlers/smtp_send.py.
// It was 10 while a batch executed on the approval request thread; LUM-710 moved
// execution to the batch worker, so the bound is now the worker's own, not a
// request timeout. A lower number here would silently cap the feature.
export const MAX_BATCH = 50;

// The worker paces sends by EMAIL_UNSUBSCRIBE_PACE_SECONDS (2 s), so rows flip
// to "Unsubscribed ✓" a few seconds apart. Polling faster than that just burns
// requests to re-render the same list.
const PROGRESS_POLL_MS = 5000;

export interface SubscriptionsViewProps {
  client: ApiClient;
  sourceId: string;
}

export function SubscriptionsView({ client, sourceId }: SubscriptionsViewProps): JSX.Element {
  const [rows, setRows] = useState<SubscriptionRow[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  // Bumped after an approval is created, to restart the poll loop without
  // waiting for the next mount.
  const [pollEpoch, setPollEpoch] = useState(0);

  const load = useCallback(
    (abort?: AbortSignal) => {
      fetchSubscriptions(client, sourceId, abort)
        .then((res) => {
          setRows(res.rows);
          setTruncated(res.truncated);
          setLoaded(true);
        })
        .catch(() => setLoaded(true));
    },
    [client, sourceId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  // LUM-710: an approved batch now drains on the worker over the following
  // minutes instead of completing inside the approval call, so without this the
  // list would sit stale showing every sender still subscribed until a manual
  // reload. Each row already renders its own "Unsubscribed ✓" — refreshing the
  // list IS the per-target progress. Polls only while a batch is in flight.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    const ctrl = new AbortController();

    const tick = () => {
      fetchUnsubscribeJobs(client, ctrl.signal)
        .then((res) => {
          if (cancelled) return;
          const active = res.jobs.some(isUnsubscribeJobActive);
          setBatchRunning(active);
          if (!active) return;
          load(ctrl.signal);
          timer = setTimeout(tick, PROGRESS_POLL_MS);
        })
        // A failed poll stops the loop rather than retrying forever — the list
        // is still correct, just not live, and the user can reload.
        .catch(() => {
          if (!cancelled) setBatchRunning(false);
        });
    };

    tick();
    return () => {
      cancelled = true;
      ctrl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [client, load, pollEpoch]);

  // Only senders offering a mailto: form can be batched — the rest need the
  // browser hand-off, so selecting them would silently do nothing.
  const selectable = useMemo(() => rows.filter((r) => r.has_mailto && !r.unsubscribed_at), [rows]);

  const toggle = (addr: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(addr)) next.delete(addr);
      else if (next.size < MAX_BATCH) next.add(addr);
      return next;
    });
  };

  const submitBatch = () => {
    const senders = [...selected];
    if (senders.length === 0) return;
    setStatus("Creating approval…");
    proposeUnsubscribe(client, sourceId, senders)
      .then((res) => {
        const skipped = res.skipped.length ? ` (${res.skipped.length} had no email form)` : "";
        // The batch is now a pending proposal — it does NOT send until approved.
        setStatus(`Sent ${res.targets.length} unsubscribe request(s) for approval${skipped}.`);
        setSelected(new Set());
        // Start watching now — approval may happen seconds from here, and the
        // batch drains gradually once it does.
        setPollEpoch((n) => n + 1);
      })
      .catch(() => setStatus("Couldn’t create the approval. Try again."));
  };

  const toggleHide = (row: SubscriptionRow) => {
    const op = row.suppressed_as
      ? clearSenderRule(client, sourceId, row.from_addr)
      : setSenderRule(client, sourceId, row.from_addr);
    op.then(() => load()).catch(() => setStatus("Couldn’t update the rule."));
  };

  if (loaded && rows.length === 0) {
    return (
      <section className="lumogis-subscriptions" aria-label="Subscriptions">
        <p className="lumogis-subscriptions__empty">
          No subscriptions found yet. Senders appear here as their mail arrives.
        </p>
      </section>
    );
  }

  return (
    <section className="lumogis-subscriptions" aria-label="Subscriptions">
      <div className="lumogis-subscriptions__toolbar">
        <button
          type="button"
          className="lumogis-subscriptions__batch"
          disabled={selected.size === 0}
          onClick={submitBatch}
        >
          Unsubscribe from {selected.size} selected
        </button>
        {selected.size >= MAX_BATCH ? (
          <span className="lumogis-subscriptions__cap" role="note">
            {MAX_BATCH} at a time — run another batch after this one.
          </span>
        ) : null}
        {batchRunning ? (
          <span
            className="lumogis-subscriptions__running"
            role="status"
            data-testid="unsubscribe-batch-running"
          >
            Unsubscribing… senders are marked off as each request sends.
          </span>
        ) : null}
      </div>

      {status ? (
        <p className="lumogis-subscriptions__status" role="status">
          {status}
        </p>
      ) : null}

      <ul className="lumogis-subscriptions__list" role="list">
        {rows.map((row) => (
          <li
            key={row.from_addr}
            className="lumogis-subscriptions__row"
            data-testid="subscription-row"
          >
            <label className="lumogis-subscriptions__select">
              <input
                type="checkbox"
                checked={selected.has(row.from_addr)}
                disabled={!selectable.includes(row)}
                onChange={() => toggle(row.from_addr)}
                aria-label={`Select ${row.from_addr}`}
              />
              <span className="lumogis-subscriptions__sender">
                {row.from_display || row.from_addr}
              </span>
            </label>

            <span className="lumogis-subscriptions__count">{row.message_count} messages</span>

            {row.unsubscribed_at ? (
              <span className="lumogis-subscriptions__done">Unsubscribed ✓</span>
            ) : null}

            {/* Rendered ONLY when the URI passes the render-side scheme gate.
                A row stored before a parser fix must not become a live link. */}
            {row.has_https && isSafeUnsubscribeUrl(row.https_uri) ? (
              <a
                className="lumogis-subscriptions__link"
                href={row.https_uri}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="unsubscribe-link"
              >
                Unsubscribe in browser ↗
              </a>
            ) : null}

            <button
              type="button"
              className="lumogis-subscriptions__hide"
              onClick={() => toggleHide(row)}
            >
              {/* "Stop hiding", never "restore": clearing a rule is go-forward
                  only — nothing already hidden can be resurfaced. */}
              {row.suppressed_as ? "Stop hiding" : "Hide future mail"}
            </button>
          </li>
        ))}
      </ul>

      {truncated ? (
        <p className="lumogis-subscriptions__truncated" role="note">
          Showing the busiest senders only.
        </p>
      ) : null}
    </section>
  );
}
