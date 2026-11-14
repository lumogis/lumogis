// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-727 — "Attachments to review": decide what gets filed, per attachment.
//
// LUM-702 filed everything from an enabled mailbox and offered undo as the
// safety net. That trade only holds when most attachments are worth keeping,
// and they are not — so this surface exists and nothing is filed without an
// explicit accept or a matching rule.
//
// GROUPED BY SENDER, and the biggest group first. The list is only affordable if
// it shrinks, and the largest single reduction available is always "decide once
// for whoever sends the most". Every group therefore offers `Always file` right
// next to its per-file checkboxes: the rule is the exit from the list, not a
// setting buried elsewhere.
//
// ASYMMETRIC OPTIMISM, and deliberately so:
//   * Decline is optimistic — no bytes move, so the only thing that can fail is
//     the write, and showing the row gone immediately is honest.
//   * Accept is NOT — it fetches, extracts and embeds each file synchronously,
//     any of which can fail per-file. Removing rows before the server says which
//     ones landed would claim filings that did not happen.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ApiClient } from "../../api/client";
import { OVERLAP_FALLBACK, paperlessOverlapWarning } from "./paperlessOverlapWarning";
import {
  acceptCandidates,
  candidateKey,
  groupKey,
  createAttachmentRule,
  fetchPaperlessOverlap,
  declineCandidates,
  explainAcceptSkip,
  explainUnreachable,
  fetchCandidates,
  type AttachmentCandidate,
  type CandidateGroup,
} from "../../api/emailAttachmentIngest";

export interface AttachmentReviewListProps {
  client: ApiClient;
  /** Scopes the list to one mailbox. Required to offer `Always file`, because a
   *  rule is per-source — there is no "this sender, everywhere" rule to make. */
  sourceId?: string;
  /** Lets a parent refresh its own counts after a decision. */
  onDecided?: () => void;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function AttachmentReviewList({
  client,
  sourceId,
  onDecided,
}: AttachmentReviewListProps): JSX.Element {
  const [groups, setGroups] = useState<CandidateGroup[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [unreachable, setUnreachable] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const load = useCallback(
    (abort?: AbortSignal) => {
      fetchCandidates(client, { sourceId }, abort)
        .then((res) => {
          setGroups(res.groups);
          setTruncated(res.truncated);
          setUnreachable(res.unreachable ?? 0);
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

  const byKey = useMemo(() => {
    const m = new Map<string, AttachmentCandidate>();
    for (const g of groups) for (const c of g.candidates) m.set(candidateKey(c), c);
    return m;
  }, [groups]);

  const refsFor = useCallback(
    (keys: Iterable<string>) =>
      [...keys]
        .map((k) => byKey.get(k))
        .filter((c): c is AttachmentCandidate => Boolean(c))
        .map((c) => ({ signal_id: c.signal_id, part_index: c.part_index })),
    [byKey],
  );

  const toggle = useCallback((key: string) => {
    setPicked((p) => {
      const next = new Set(p);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const toggleGroup = useCallback((group: CandidateGroup) => {
    setPicked((p) => {
      const next = new Set(p);
      const keys = group.candidates.map(candidateKey);
      const allOn = keys.every((k) => next.has(k));
      for (const k of keys) {
        if (allOn) next.delete(k);
        else next.add(k);
      }
      return next;
    });
  }, []);

  /** Drop decided keys from the groups without a refetch, so the list does not
   *  jump under the user mid-review. */
  const removeKeys = useCallback((keys: Set<string>) => {
    setGroups((gs) =>
      gs
        .map((g) => {
          const candidates = g.candidates.filter((c) => !keys.has(candidateKey(c)));
          return { ...g, candidates, count: candidates.length };
        })
        .filter((g) => g.candidates.length > 0),
    );
  }, []);

  const onAccept = useCallback(
    (keys: Set<string>) => {
      const refs = refsFor(keys);
      if (refs.length === 0) return;
      setBusy(true);
      // NOT optimistic — see the header. Each file is a real fetch+extract+embed
      // and any of them can fail on its own.
      acceptCandidates(client, refs)
        .then((res) => {
          const failed = new Set(
            res.skipped.map((s) => `${s.signal_id}:${s.part_index}`),
          );
          removeKeys(new Set([...keys].filter((k) => !failed.has(k))));
          setPicked((p) => new Set([...p].filter((k) => failed.has(k))));
          if (res.skipped.length > 0) {
            // Named, not counted. "3 filed, 1 skipped" leaves the user hunting
            // for which one, and the reason is the actionable part.
            const first = res.skipped[0];
            setStatus(
              `Filed ${res.accepted}. ${res.skipped.length} not filed — ` +
                `${explainAcceptSkip(first.reason)}`,
            );
          } else {
            setStatus(`Filed ${res.accepted} to your library`);
          }
          onDecided?.();
        })
        .catch(() => setStatus("Couldn't file those — try again"))
        .finally(() => setBusy(false));
    },
    [client, refsFor, removeKeys, onDecided],
  );

  const onDecline = useCallback(
    (keys: Set<string>) => {
      const refs = refsFor(keys);
      if (refs.length === 0) return;
      const before = groups;
      setBusy(true);
      // Optimistic: no bytes move, so only the write can fail.
      removeKeys(keys);
      setPicked((p) => new Set([...p].filter((k) => !keys.has(k))));
      declineCandidates(client, refs)
        .then((res) => {
          setStatus(
            `Won't file ${res.declined}. You can undo this from Filed from email.`,
          );
          onDecided?.();
        })
        .catch(() => {
          setGroups(before);
          setStatus("Couldn't save that — try again");
        })
        .finally(() => setBusy(false));
    },
    [client, groups, refsFor, removeKeys, onDecided],
  );

  // LUM-728 — one probe per MAILBOX, not per sender group. The overlap is a fact
  // about the mailbox; asking once per sender would ask the same question
  // repeatedly and get the same answer.
  //
  // LUM-742 made all three of these per-source. The list can now span mailboxes,
  // and every one of them was written assuming it could not:
  //   * `advisory` was a single string, so mailbox A's warning rendered above
  //     mailbox B's groups.
  //   * `overlapAsk` was keyed on `from_addr`, so the same sender in two
  //     mailboxes showed one refusal on both.
  //   * `ackedRef` was a single boolean whose ONLY reset was the `[sourceId]`
  //     effect below — and on a cross-mailbox list `sourceId` is permanently
  //     undefined, so acknowledging for A would have sent
  //     `acknowledge_paperless_overlap: true` for a rule on B. The server skips
  //     its probe whenever that flag is true, for any source, so the client is
  //     the only thing binding an acknowledgement to a mailbox.
  const [advisory, setAdvisory] = useState<Record<string, string>>({});
  const [overlapAsk, setOverlapAsk] = useState<
    Record<string, { message: string; alternative: string; recovery: string }>
  >({});
  // Acknowledgement is held for the life of the list and never persisted:
  // nothing is stored server-side (ADR D2), and a fresh look at the list is a
  // fresh decision.
  const ackedRef = useRef<Set<string>>(new Set());

  // Reset on a scoped mount when the mailbox changes. The cross-mailbox mount
  // passes no `sourceId`, so this fires once — which is why the state above is
  // keyed per source rather than relying on this to clear it.
  useEffect(() => {
    ackedRef.current = new Set();
    setAdvisory({});
    setOverlapAsk({});
  }, [sourceId]);

  // The mailboxes actually represented in the list. On a scoped mount this is
  // just `sourceId`; on the cross-mailbox route it is whatever came back.
  const shownSourceIds = useMemo(
    () => Array.from(new Set(groups.map((g) => g.source_id).filter(Boolean))),
    [groups],
  );
  const shownKey = shownSourceIds.join(",");
  const multiSource = shownSourceIds.length > 1;

  useEffect(() => {
    if (!loaded || shownSourceIds.length === 0) return;
    let live = true;
    // Was `if (!loaded || !sourceId) return;` — which on the cross-mailbox route
    // returned early forever, so the paperless advisory silently never fired and
    // nothing on screen showed that it had stopped.
    shownSourceIds.forEach((sid) => {
      fetchPaperlessOverlap(client, sid)
        .then((r) => {
          const msg = r.message;
          if (live && r.overlap && r.confidence === "low" && msg) {
            setAdvisory((prev) => ({ ...prev, [sid]: msg }));
          }
        })
        .catch(() => {
          /* advisory only — a failed probe must never disturb the list */
        });
    });
    return () => {
      live = false;
    };
    // Depends on the JOINED id list, not the array: a refetch that returns the
    // same mailboxes yields a new array identity every time and would re-probe
    // paperless on every poll. `shownSourceIds` is derived from `shownKey`, so
    // the omission is safe rather than an oversight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, loaded, shownKey]);

  const onAlwaysFile = useCallback(
    (group: CandidateGroup) => {
      // The GROUP's mailbox, not the page's. A rule belongs to exactly one
      // mailbox and a group is now (sender, source), so this is unambiguous
      // even on a list spanning several.
      if (!group.source_id) return;
      setBusy(true);
      createAttachmentRule(
        client,
        group.source_id,
        "sender",
        group.from_addr,
        undefined,
        ackedRef.current.has(group.source_id),
      )
        .then(() => {
          // Creating the rule does NOT file the pending ones — the rule is
          // consulted by the sweep over future arrivals. Filing them here is
          // what the user means by "always", so both happen.
          const keys = new Set(group.candidates.map(candidateKey));
          setStatus(
            `Attachments from ${group.from_display || group.from_addr} will be filed automatically`,
          );
          onAccept(keys);
        })
        .catch((err: unknown) => {
          // LUM-728 — the overlap refusal is a QUESTION, not a failure. Nothing
          // went wrong, so the generic retry copy would be a lie; and crucially
          // the pending attachments must stay untouched, because filing them
          // while refusing the rule would half-apply an action the user has not
          // confirmed.
          const warning = paperlessOverlapWarning(err);
          if (warning) {
            // `warning.message` falls back to "" when the server omits it, and a
            // truthiness check on that would re-render the plain button: the
            // rule then becomes uncreatable with no feedback at all, because the
            // next click 422s again. Store a guaranteed-non-empty string.
            setOverlapAsk((prev) => ({
              ...prev,
              [groupKey(group)]: {
                message: warning.message || OVERLAP_FALLBACK,
                alternative: warning.alternative,
                recovery: warning.recovery,
              },
            }));
            setBusy(false);
            return;
          }
          setStatus("Couldn't save that rule — try again");
          setBusy(false);
        });
    },
    [client, onAccept],
  );

  // Second click: the answer. Acknowledging covers the mailbox it answers for —
  // which is the scope the overlap question is asked at — and NOT the whole
  // list, which since LUM-742 may span several mailboxes.
  //
  // Since LUM-743 the server checks this rather than trusting it: the request
  // names the mailbox its acknowledgement is about, and a mismatch is treated
  // as no acknowledgement at all. So a regression here re-prompts the user
  // instead of silently filing everything twice on another mailbox.
  const onFileAnyway = useCallback(
    (group: CandidateGroup) => {
      ackedRef.current = new Set(ackedRef.current).add(group.source_id);
      // Clear only the refusal being answered. Clearing all of them wiped other
      // mailboxes' pending refusals back to a plain button, and their next click
      // simply 422s again.
      setOverlapAsk((prev) => {
        const next = { ...prev };
        delete next[groupKey(group)];
        return next;
      });
      onAlwaysFile(group);
    },
    [onAlwaysFile],
  );

  if (!loaded) {
    return <p data-testid="review-loading">Loading…</p>;
  }

  if (groups.length === 0) {
    return (
      <section aria-label="Attachments to review">
        {/* The status has to survive into the empty state. Deciding on the LAST
            attachment is exactly when the confirmation matters most — and an
            early return that drops it means the one action with no feedback is
            the one that finished the job. */}
        {status ? (
          <p role="status" data-testid="review-status">
            {status}
          </p>
        ) : null}
        <p data-testid="review-empty">
          Nothing to review. New attachments will show up here.
        </p>
      {unreachable > 0 ? (
        // Terminal, but not silent. The bytes are unfetchable so there is no
        // action to offer — the point is that the user stops waiting for a file
        // that is never coming, and knows why.
        <p data-testid="review-unreachable">{explainUnreachable(unreachable)}</p>
      ) : null}
      </section>
    );
  }

  return (
    <section aria-label="Attachments to review">
      {/* LUM-728 — a `low`-confidence overlap. Rendered ONCE for the list, never
          per sender group, and never blocking: a wrong `low` is frequent (two
          mailboxes on one provider is ordinary), so gating on it would train
          dismissal — and the dismissal transfers to the `high` gate that
          mattered. It is honest about its own uncertainty, which a modal
          cannot be. */}
      {/* Advisories are per mailbox now — rendered with the groups they describe,
          not once above a list that may span several. */}
      {status ? (
        <p role="status" data-testid="review-status">
          {status}
        </p>
      ) : null}
      {unreachable > 0 ? (
        // Terminal, but not silent. The bytes are unfetchable so there is no
        // action to offer — the point is that the user stops waiting for a file
        // that is never coming, and knows why.
        <p data-testid="review-unreachable">{explainUnreachable(unreachable)}</p>
      ) : null}
      {truncated ? (
        // Never a silent cut — a user who cannot see the rest must at least know
        // there is a rest, or they will believe they finished.
        <p data-testid="review-truncated">
          Showing the most recent. Decide on these and more will appear.
        </p>
      ) : null}
      {picked.size > 0 ? (
        <div data-testid="review-bulk-bar">
          <span data-testid="review-picked-count">{picked.size} selected</span>
          <button
            type="button"
            onClick={() => onAccept(picked)}
            disabled={busy}
            data-testid="review-accept-picked"
          >
            File to library
          </button>
          <button
            type="button"
            onClick={() => onDecline(picked)}
            disabled={busy}
            data-testid="review-decline-picked"
          >
            Don't file
          </button>
        </div>
      ) : null}
      <ul data-testid="review-groups">
        {groups.map((group) => {
          const keys = group.candidates.map(candidateKey);
          const allOn = keys.length > 0 && keys.every((k) => picked.has(k));
          return (
            <li key={groupKey(group)} data-testid={`review-group-${groupKey(group)}`}>
              <h3>
                <label>
                  <input
                    type="checkbox"
                    checked={allOn}
                    onChange={() => toggleGroup(group)}
                    aria-label={`Select all from ${group.from_display || group.from_addr}`}
                  />
                  {group.from_display || group.from_addr}
                </label>
                {/* Only when the list actually spans mailboxes. A one-mailbox
                    household gains nothing from a label that is the same on
                    every row, and a badge that never varies is noise. */}
                {multiSource && group.source_name ? (
                  <span data-testid={`review-group-source-${groupKey(group)}`}>
                    {group.source_name}
                  </span>
                ) : null}
                <span data-testid="review-group-count">{group.count}</span>
              </h3>
              {/* This mailbox's low-confidence paperless advisory, beside the
                  groups it describes rather than once above a mixed list. */}
              {advisory[group.source_id] ? (
                <p data-testid={`review-overlap-advisory-${group.source_id}`}>
                  {advisory[group.source_id]}
                </p>
              ) : null}
              {/* The exit from the list. Placed with the group rather than in a
                  settings page because this is the moment the user knows the
                  answer — they are looking at four invoices from one sender. */}
              {group.source_id ? (
                overlapAsk[groupKey(group)] ? (
                  <span data-testid={`review-overlap-${groupKey(group)}`}>
                    <span>{overlapAsk[groupKey(group)].message}</span>
                    {/* The refusal has three parts and only showing the first
                        turns a considered explanation into a bare error. */}
                    {overlapAsk[groupKey(group)].alternative ? (
                      <span data-testid={`review-overlap-alt-${groupKey(group)}`}>
                        {overlapAsk[groupKey(group)].alternative}
                      </span>
                    ) : null}
                    {overlapAsk[groupKey(group)].recovery ? (
                      <span data-testid={`review-overlap-recovery-${groupKey(group)}`}>
                        {overlapAsk[groupKey(group)].recovery}
                      </span>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => onFileAnyway(group)}
                      disabled={busy}
                      data-testid={`review-overlap-confirm-${groupKey(group)}`}
                    >
                      File anyway
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onAlwaysFile(group)}
                    disabled={busy}
                    data-testid={`review-always-${groupKey(group)}`}
                  >
                    Always file from this sender
                  </button>
                )
              ) : null}
              <ul>
                {group.candidates.map((c) => {
                  const key = candidateKey(c);
                  return (
                    <li key={key} data-testid={`review-row-${key}`}>
                      <label>
                        <input
                          type="checkbox"
                          checked={picked.has(key)}
                          onChange={() => toggle(key)}
                          aria-label={`Select ${c.filename}`}
                        />
                        <span data-testid="review-filename">{c.filename}</span>
                      </label>
                      <span data-testid="review-size">{formatBytes(c.size_bytes)}</span>
                      {/* Subject is what makes a file called `scan_0043.pdf`
                          decidable at all. */}
                      {c.subject ? (
                        <span data-testid="review-subject">{c.subject}</span>
                      ) : null}
                      <time dateTime={c.received_at}>{formatWhen(c.received_at)}</time>
                      <button
                        type="button"
                        onClick={() => onAccept(new Set([key]))}
                        disabled={busy}
                        aria-label={`File ${c.filename} to library`}
                      >
                        File
                      </button>
                      <button
                        type="button"
                        onClick={() => onDecline(new Set([key]))}
                        disabled={busy}
                        aria-label={`Don't file ${c.filename}`}
                      >
                        Skip
                      </button>
                    </li>
                  );
                })}
              </ul>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
