// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-649: durable, browsable "what I've tuned" view of feedback-learned
// relevance weights. Trust requires the effect of 👍/👎 to stay inspectable, not
// just glimpsed once in a toast — this is the read-only companion to that toast.
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import {
  fetchLearnedRelevanceWeights,
  type LearnedWeightsResponse,
  type LearnedWeightTerm,
} from "../../api/relevanceProfile";

const AXIS_META: ReadonlyArray<{ key: keyof LearnedWeightsResponse; label: string }> = [
  { key: "topics", label: "Topics" },
  { key: "entities", label: "People & senders" },
  { key: "categories", label: "Categories" },
];

function isEmpty(data: LearnedWeightsResponse): boolean {
  return AXIS_META.every((a) => data[a.key].length === 0);
}

function TermList({ terms }: { terms: LearnedWeightTerm[] }): JSX.Element {
  const demoted = terms.filter((t) => t.weight < 0);
  const promoted = terms.filter((t) => t.weight > 0);
  return (
    <>
      {demoted.length > 0 ? (
        <div className="lumogis-relevance__group" data-testid="demoted-group">
          <h4>Flagging fewer like these</h4>
          <ul>
            {demoted.map((t) => (
              <li key={t.term}>{t.term}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {promoted.length > 0 ? (
        <div className="lumogis-relevance__group" data-testid="promoted-group">
          <h4>Flagging more like these</h4>
          <ul>
            {promoted.map((t) => (
              <li key={t.term}>{t.term}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}

export function MeRelevanceView(): JSX.Element {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ["relevance", "learned"],
    queryFn: () => fetchLearnedRelevanceWeights(client),
  });

  return (
    <section className="lumogis-relevance" data-testid="me-relevance-view">
      <h3>Relevance tuning</h3>
      <p className="lumogis-relevance__intro">
        As you use 👍 and “Not important” on emails, Lumogis learns which topics,
        people, and categories matter to you. Everything it has learned is listed
        here — nothing is hidden.
      </p>

      {q.isLoading ? (
        <p role="status">Loading…</p>
      ) : q.isError ? (
        <p role="alert">Couldn't load your tuning. Retry later.</p>
      ) : q.data && isEmpty(q.data) ? (
        <p className="lumogis-relevance__empty" data-testid="relevance-empty">
          Nothing tuned yet — use 👍 / “Not important” on emails to teach Lumogis
          what matters.
        </p>
      ) : q.data ? (
        AXIS_META.map((a) =>
          q.data[a.key].length > 0 ? (
            <div key={a.key} className="lumogis-relevance__axis" data-axis={a.key}>
              <h3>{a.label}</h3>
              <TermList terms={q.data[a.key]} />
            </div>
          ) : null,
        )
      ) : null}
    </section>
  );
}
