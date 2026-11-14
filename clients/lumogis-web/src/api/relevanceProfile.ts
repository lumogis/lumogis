// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-649: feedback → relevance learning API façade.
//
// Bare paths (`/feedback`, `/profile/learned`) — the signals router is mounted
// WITHOUT an /api/v1 prefix, same as GET /signals and the triage mutations.
import type { ApiClient } from "./client";

// Response of POST /feedback for item_type="signal" explicit feedback. The
// `effect` names which learned term(s) moved; absent for entity/implicit feedback.
export interface SignalFeedbackResponse {
  status: "ok";
  type: "explicit" | "implicit";
  positive?: boolean;
  effect?: { demoted: string[]; promoted: string[] };
}

export interface LearnedWeightTerm {
  term: string;
  weight: number;
}

// GET /profile/learned — sparse (zero-weight terms omitted), demoted-first per axis.
export interface LearnedWeightsResponse {
  topics: LearnedWeightTerm[];
  entities: LearnedWeightTerm[];
  categories: LearnedWeightTerm[];
}

// 👍/👎 on a signal. positive=true promotes, false demotes the matching terms.
export function postSignalFeedback(
  client: ApiClient,
  params: { itemId: string; positive: boolean },
): Promise<SignalFeedbackResponse> {
  return client.postJson("/feedback", {
    item_type: "signal",
    item_id: params.itemId,
    positive: params.positive,
  });
}

export function fetchLearnedRelevanceWeights(
  client: ApiClient,
): Promise<LearnedWeightsResponse> {
  return client.getJson("/profile/learned");
}
