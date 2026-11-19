// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Search the mail Lumogis holds (LUM-819).
import { useCallback, useMemo, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import type { ApiClient } from "../../api/client";
import {
  MIN_SEARCH_CHARS,
  searchEmail,
  type EmailSearchHit,
  type EmailSearchResponse,
  type FieldCounts,
  type SearchFields,
} from "../../api/emailSearch";
import { collapseProjections } from "./triage";

const FIELD_CHIPS: ReadonlyArray<{ key: SearchFields; label: string }> = [
  { key: "all", label: "All" },
  { key: "subject", label: "Subject" },
  { key: "sender", label: "Sender" },
  { key: "body", label: "Message" },
];

function countFor(counts: FieldCounts | null, key: SearchFields): number {
  if (!counts) return 0;
  return key === "all" ? counts.all : counts[key];
}

/** "200+" rather than a number the server did not actually finish counting. */
function renderCount(n: number, capped: boolean): string {
  return capped && n > 0 ? `${n}+` : String(n);
}

export interface EmailSearchBarProps {
  client: ApiClient;
  sourceId?: string;
  /** Rendered for each hit; the parent owns row presentation and triage. */
  renderHit: (hit: EmailSearchHit) => JSX.Element;
  /** Told when a search becomes active, so the lanes can step aside. */
  onActiveChange?: (active: boolean) => void;
}

export function EmailSearchBar({
  client,
  sourceId,
  renderHit,
  onActiveChange,
}: EmailSearchBarProps): JSX.Element {
  const [q, setQ] = useState("");
  const [fields, setFields] = useState<SearchFields>("all");
  const [result, setResult] = useState<EmailSearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tooShort = q.trim().length > 0 && q.trim().length < MIN_SEARCH_CHARS;

  const run = useCallback(
    async (nextFields: SearchFields) => {
      const query = q.trim();
      if (query.length < MIN_SEARCH_CHARS) return;
      setBusy(true);
      setError(null);
      try {
        const res = await searchEmail(client, query, { fields: nextFields, sourceId });
        setResult(res);
        onActiveChange?.(true);
      } catch {
        // Clear the previous query's results rather than leaving them under a
        // new query's text — stale hits presented as an answer are worse than
        // no answer, and this surface exists to stop search failing quietly.
        setResult(null);
        onActiveChange?.(false);
        // The query text is never echoed into an error message: a search query
        // is as sensitive as the mail it searches.
        setError("Search failed. Try again.");
      } finally {
        setBusy(false);
      }
    },
    [client, q, sourceId, onActiveChange],
  );

  const clear = useCallback(() => {
    setQ("");
    setResult(null);
    setError(null);
    setFields("all");
    onActiveChange?.(false);
  }, [onActiveChange]);

  const pickField = useCallback(
    (key: SearchFields) => {
      setFields(key);
      void run(key);
    },
    [run],
  );

  // Without this an author sees their own shared mail twice — once as the
  // personal original and once as the projection (LUM-661).
  const hits = useMemo(
    () => (result ? (collapseProjections(result.hits) as EmailSearchHit[]) : []),
    [result],
  );

  return (
    <section aria-label="Mail search" className="mb-4 flex flex-col gap-3">
      <form
        role="search"
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void run(fields);
        }}
      >
        <Input
          type="search"
          className="min-w-48 flex-1"
          value={q}
          aria-label="Search mail"
          placeholder="Search sender, subject or message text"
          onChange={(e) => setQ(e.target.value)}
        />
        <Button type="submit" disabled={busy || q.trim().length < MIN_SEARCH_CHARS}>
          {busy ? "Searching…" : "Search"}
        </Button>
        {result !== null && (
          <Button type="button" variant="outline" onClick={clear}>
            Clear
          </Button>
        )}
      </form>

      {tooShort && <p role="status" className="m-0 text-sm text-muted-foreground">Type at least {MIN_SEARCH_CHARS} characters.</p>}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {result !== null && (
        <>
          {/*
            The counts are the feature, not a garnish. Subject and sender match
            on substrings; message text matches whole words. A user cannot see
            which one answered, and an empty result looks the same as an absent
            message — so showing "Message 0" beside "Subject 3" is what makes
            the difference legible without any help text.
          */}
          <ToggleGroup
            variant="outline"
            size="sm"
            spacing={1}
            aria-label="Narrow by field"
            className="flex-wrap"
            value={[fields]}
            onValueChange={(next: unknown[]) => {
              // Pressing the selected chip again would clear the group; keep it.
              const picked = next[0];
              if (typeof picked === "string") pickField(picked as SearchFields);
            }}
          >
            {FIELD_CHIPS.map(({ key, label }) => (
              <ToggleGroupItem key={key} value={key}>
                {label} {renderCount(countFor(result.counts, key), result.counts.capped)}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>

          {hits.length === 0 ? (
            <p role="status" className="m-0 text-sm text-muted-foreground">
              No matches.
              {result.counts.body === 0 && fields === "body" && (
                <> Message text is only searchable for mailboxes set to full ingest.</>
              )}
              {result.searched_from && (
                <> Lumogis holds mail from {String(result.searched_from).slice(0, 10)} onwards.</>
              )}
            </p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {hits.map((hit) => (
                <li key={hit.signal_id}>
                  {renderHit(hit)}
                  {hit.matched_in.includes("body") && hit.snippet && (
                    <p className="m-0 mt-1 text-sm text-muted-foreground">
                      <q>{hit.snippet}</q>
                      {hit.body_truncated && <> (this message was indexed in part)</>}
                    </p>
                  )}
                  {/*
                    A summary match is Lumogis's own paraphrase of the subject,
                    not an excerpt from the message. Saying so keeps a generated
                    sentence from reading like the sender's words.
                  */}
                  {hit.matched_in.includes("summary") &&
                    !hit.matched_in.includes("subject") && (
                      <p className="m-0 mt-1 text-xs text-muted-foreground">Matched Lumogis's summary</p>
                    )}
                </li>
              ))}
            </ul>
          )}

          {result.truncated && <p role="status" className="m-0 text-sm text-muted-foreground">More matches than shown — narrow your search.</p>}
        </>
      )}
    </section>
  );
}
