// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-649 — feedback → relevance learning API façade.
import { describe, expect, it, vi } from "vitest";

import {
  fetchLearnedRelevanceWeights,
  postSignalFeedback,
} from "../../src/api/relevanceProfile";
import type { ApiClient } from "../../src/api/client";

describe("postSignalFeedback", () => {
  it("POSTs /feedback with item_type=signal and the effect comes back", async () => {
    const postJson = vi.fn().mockResolvedValue({
      status: "ok",
      type: "explicit",
      positive: false,
      effect: { demoted: ["newsletter"], promoted: [] },
    });
    const client = { postJson } as unknown as ApiClient;

    const res = await postSignalFeedback(client, { itemId: "sig-1", positive: false });

    expect(postJson).toHaveBeenCalledWith("/feedback", {
      item_type: "signal",
      item_id: "sig-1",
      positive: false,
    });
    expect(res.effect?.demoted).toEqual(["newsletter"]);
  });
});

describe("fetchLearnedRelevanceWeights", () => {
  it("GETs /profile/learned and returns the typed shape", async () => {
    const getJson = vi.fn().mockResolvedValue({
      topics: [{ term: "work", weight: 0.3 }],
      entities: [],
      categories: [{ term: "newsletter", weight: -0.6 }],
    });
    const client = { getJson } as unknown as ApiClient;

    const res = await fetchLearnedRelevanceWeights(client);

    expect(getJson).toHaveBeenCalledWith("/profile/learned");
    expect(res.topics[0].term).toBe("work");
    expect(res.categories[0].weight).toBe(-0.6);
  });
});
