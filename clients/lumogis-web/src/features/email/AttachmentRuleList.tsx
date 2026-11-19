// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-727 — the rules that file attachments without asking again.
//
// Rules are created in the flow, from the review list ("Always file from this
// sender"), because that is the moment the user knows the answer. This surface
// exists for the other half: seeing what you agreed to, and taking it back. A
// rule that files silently and cannot be found is worse than no rule.
//
// TWO THINGS ARE SAID OUT LOUD HERE, and both correct a reasonable wrong guess:
//
//   * Deleting a rule does NOT unfile anything. `filed_by_rule_id` is ON DELETE
//     SET NULL, so those documents keep their undo path and stay in the library.
//     "Delete rule" reads like a retraction; the copy has to say it is not.
//   * A STALE rule is one whose category no longer exists. It cannot fire. It is
//     surfaced rather than swept, because the user deleted a category — not a
//     filing instruction — and quietly removing their rule would be us deciding
//     what they meant.

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { ApiClient } from "../../api/client";
import {
  deleteAttachmentRule,
  DELETE_RULE_EXPLANATION,
  fetchAttachmentRules,
  fetchPaperlessOverlapAll,
  STALE_RULE_EXPLANATION,
  type AttachmentRule,
} from "../../api/emailAttachmentIngest";

// LUM-836 — one key for every mounted copy of this component.
//
// `EmailSignalsView` renders one of these per mailbox card, inside a
// `keepMounted` Collapsible — which mounts its children whether or not the
// disclosure is open, as `<details>` did.
// A per-source query key would therefore issue one paperless config fetch per
// mailbox, which is precisely the pattern LUM-744 exists to remove, recreated on
// a new surface. Sharing the key makes N mounts one request.
//
// `existing` is IN the key, not just in the request: a future `creating`
// consumer must not read this cache entry and render the wrong tense.
const OVERLAP_KEY = ["paperless", "overlap", "all", "existing"] as const;

export interface AttachmentRuleListProps {
  client: ApiClient;
  sourceId: string;
}

function describe(rule: AttachmentRule): string {
  return rule.match_type === "sender"
    ? `Always file attachments from ${rule.match_value}`
    : `Always file attachments in ${rule.match_value}`;
}

export function AttachmentRuleList({
  client,
  sourceId,
}: AttachmentRuleListProps): JSX.Element {
  const [rules, setRules] = useState<AttachmentRule[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState("");
  const [pending, setPending] = useState<Set<string>>(new Set());

  useEffect(() => {
    const ctrl = new AbortController();
    fetchAttachmentRules(client, sourceId, ctrl.signal)
      .then((res) => {
        setRules(res.rules);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
    return () => ctrl.abort();
  }, [client, sourceId]);

  // Gated on the mailbox actually having rules: no rules means nothing can be
  // filed twice, so the probe would cost a paperless round-trip to answer a
  // question that cannot apply. Most users are in that case.
  const overlapQ = useQuery({
    queryKey: OVERLAP_KEY,
    queryFn: () => fetchPaperlessOverlapAll(client, "existing"),
    enabled: loaded && rules.length > 0,
    // Same window as LUM-831's sending addresses. `retry` is deliberately not
    // set here: `AuthProvider` already defaults it to false app-wide, and
    // setting it again would read as this query having a reason to differ.
    staleTime: 5 * 60 * 1000,
  });
  const overlapMessage =
    overlapQ.data?.find((i) => i.source_id === sourceId && i.overlap)?.message ?? null;

  const onDelete = useCallback(
    (rule: AttachmentRule) => {
      const before = rules;
      setPending((p) => new Set(p).add(rule.id));
      // Optimistic: deleting a rule moves no documents, so the only thing that
      // can fail is the write.
      setRules((rs) => rs.filter((r) => r.id !== rule.id));
      deleteAttachmentRule(client, rule.id)
        .then(() => setStatus(`Stopped. ${DELETE_RULE_EXPLANATION}`))
        .catch(() => {
          setRules(before);
          setStatus("Couldn't remove that rule — try again");
        })
        .finally(() =>
          setPending((p) => {
            const next = new Set(p);
            next.delete(rule.id);
            return next;
          }),
        );
    },
    [client, rules],
  );

  if (!loaded) {
    return (
      <p data-testid="rules-loading" className="m-0 text-sm text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (rules.length === 0) {
    return (
      <section aria-label="Automatic filing rules" className="grid gap-2">
        {/* Deleting the LAST rule must still confirm what it did — and what it
            did not do to the documents that rule already filed. An early return
            here would drop that reassurance precisely when it is needed. */}
        {status ? (
          <p role="status" data-testid="rules-status" className="m-0 text-sm text-muted-foreground">
            {status}
          </p>
        ) : null}
        <p data-testid="rules-empty" className="m-0 text-sm text-muted-foreground">
          No automatic filing rules. You can add one while reviewing attachments.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Automatic filing rules" className="grid gap-2">
      {status ? (
        <p role="status" data-testid="rules-status" className="m-0 text-sm text-muted-foreground">
          {status}
        </p>
      ) : null}
      {/* Rendered only on a positive finding. A failed probe (`auth` /
          `unavailable`) renders NOTHING rather than implying all-clear —
          ADR-192 records that `status: "ok"` is not proof of non-overlap,
          because paperless permission-filters to an empty list and a truncated
          page reports `unavailable`. Silence is the honest degrade; a reassuring
          absence of warning is not. Not a gate either: these rules already
          exist, so there is nothing to consent to. */}
      {overlapMessage ? (
        <p data-testid="rules-paperless-overlap" className="m-0 text-sm">{overlapMessage}</p>
      ) : null}
      <ul data-testid="rules-list" className="m-0 grid list-none gap-1 p-0">
        {rules.map((rule) => (
          <li
            key={rule.id}
            data-testid={`rule-${rule.id}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b py-2 text-sm last:border-b-0"
          >
            <span data-testid="rule-description" className="min-w-0 flex-1 basis-48 break-words">{describe(rule)}</span>
            {rule.stale ? (
              <span data-testid="rule-stale" className="w-full text-muted-foreground">{STALE_RULE_EXPLANATION}</span>
            ) : null}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onDelete(rule)}
              disabled={pending.has(rule.id)}
              aria-label={`Stop ${describe(rule)}`}
              title={DELETE_RULE_EXPLANATION}
            >
              Stop
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
