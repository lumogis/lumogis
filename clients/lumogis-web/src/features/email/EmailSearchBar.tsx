// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Search the mail Lumogis holds (LUM-819).
import { useCallback, useMemo, useState } from "react";

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
    <section aria-label="Mail search" className="lumogis-email-search">
      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          void run(fields);
        }}
      >
        <input
          type="search"
          value={q}
          aria-label="Search mail"
          placeholder="Search sender, subject or message text"
          onChange={(e) => setQ(e.target.value)}
        />
        <button type="submit" disabled={busy || q.trim().length < MIN_SEARCH_CHARS}>
          {busy ? "Searching…" : "Search"}
        </button>
        {result !== null && (
          <button type="button" onClick={clear}>
            Clear
          </button>
        )}
      </form>

      {tooShort && <p role="status">Type at least {MIN_SEARCH_CHARS} characters.</p>}
      {error && <p role="alert">{error}</p>}

      {result !== null && (
        <>
          {/*
            The counts are the feature, not a garnish. Subject and sender match
            on substrings; message text matches whole words. A user cannot see
            which one answered, and an empty result looks the same as an absent
            message — so showing "Message 0" beside "Subject 3" is what makes
            the difference legible without any help text.
          */}
          <nav aria-label="Narrow by field" className="lumogis-email-search__fields">
            {FIELD_CHIPS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                aria-pressed={fields === key}
                onClick={() => pickField(key)}
              >
                {label} {renderCount(countFor(result.counts, key), result.counts.capped)}
              </button>
            ))}
          </nav>

          {hits.length === 0 ? (
            <p role="status">
              No matches.
              {result.counts.body === 0 && fields === "body" && (
                <> Message text is only searchable for mailboxes set to full ingest.</>
              )}
              {result.searched_from && (
                <> Lumogis holds mail from {String(result.searched_from).slice(0, 10)} onwards.</>
              )}
            </p>
          ) : (
            <ul className="lumogis-email-search__results">
              {hits.map((hit) => (
                <li key={hit.signal_id}>
                  {renderHit(hit)}
                  {hit.matched_in.includes("body") && hit.snippet && (
                    <p className="lumogis-email-search__snippet">
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
                      <p className="lumogis-email-search__why">Matched Lumogis's summary</p>
                    )}
                </li>
              ))}
            </ul>
          )}

          {result.truncated && <p role="status">More matches than shown — narrow your search.</p>}
        </>
      )}
    </section>
  );
}
