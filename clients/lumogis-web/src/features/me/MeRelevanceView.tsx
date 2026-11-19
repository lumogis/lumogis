// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-649: durable, browsable "what I've tuned" view of feedback-learned
// relevance weights. Trust requires the effect of 👍/👎 to stay inspectable, not
// just glimpsed once in a toast — this is the read-only companion to that toast.
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Badge } from "../../components/ui/badge";
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
    <div className="flex flex-col gap-3">
      {demoted.length > 0 ? (
        <div data-testid="demoted-group">
          <h3 className="m-0 mb-1 text-sm font-semibold">Flagging fewer like these</h3>
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
            {demoted.map((t) => (
              <li key={t.term}>
                <Badge variant="secondary">{t.term}</Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {promoted.length > 0 ? (
        <div data-testid="promoted-group">
          <h3 className="m-0 mb-1 text-sm font-semibold">Flagging more like these</h3>
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
            {promoted.map((t) => (
              <li key={t.term}>
                <Badge variant="secondary">{t.term}</Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function MeRelevanceView(): JSX.Element {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ["relevance", "learned"],
    queryFn: () => fetchLearnedRelevanceWeights(client),
  });

  return (
    <div data-testid="me-relevance-view">
      <p className="m-0 mb-4 max-w-2xl text-sm text-muted-foreground">
        As you use 👍 and “Not important” on emails, Lumogis learns which topics,
        people, and categories matter to you. Everything it has learned is listed
        here — nothing is hidden.
      </p>

      {q.isLoading ? (
        <LoadingPlaceholder label="Loading what Lumogis has learned…" className="max-w-2xl">
          <SkeletonText lines={4} />
        </LoadingPlaceholder>
      ) : q.isError ? (
        <ErrorState message="Lumogis couldn't load what it has learned." onRetry={() => void q.refetch()} />
      ) : q.data && isEmpty(q.data) ? (
        <div data-testid="relevance-empty">
          <EmptyState
            title="Nothing tuned yet"
            helperText="Use 👍 / “Not important” on emails to teach Lumogis what matters."
          />
        </div>
      ) : q.data ? (
        AXIS_META.map((a) =>
          q.data[a.key].length > 0 ? (
            <div key={a.key} data-axis={a.key}>
              <PageSection title={a.label}>
                <TermList terms={q.data[a.key]} />
              </PageSection>
            </div>
          ) : null,
        )
      ) : null}
    </div>
  );
}
