// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-836 — the paperless overlap line on the rules page.
//
// Three of these assert absences, and that is the point. Two review rounds on
// this feature found the same defect shape three times: a surface that runs,
// returns cleanly, and shows the user nothing. Positive assertions passed
// against every one of them.
//
//   * `issues ONE request` — the natural implementation is a per-source query
//     key, and it satisfies every other test in this file. `EmailSignalsView`
//     mounts one of these per mailbox card inside a `<details>`, and React
//     mounts those children whether or not the disclosure is open, so a
//     per-source key means one paperless config fetch per mailbox — the exact
//     pattern LUM-744 exists to remove.
//   * `renders nothing when the probe failed` — an advisory that quietly
//     asserts safety when it could not check is worse than no advisory.
//   * `no request when the mailbox has no rules` — a mailbox that files nothing
//     cannot duplicate anything.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// `vi.hoisted` because `vi.mock` is lifted above every declaration in the file,
// including the ESM imports below — a plain `const` would not exist yet when the
// factory runs.
const api = vi.hoisted(() => ({
  fetchAttachmentRules: vi.fn(),
  deleteAttachmentRule: vi.fn(),
  fetchPaperlessOverlapAll: vi.fn(),
  DELETE_RULE_EXPLANATION: "Deleting a rule does not unfile anything.",
  STALE_RULE_EXPLANATION: "This rule's category no longer exists.",
}));
vi.mock("../../../src/api/emailAttachmentIngest", () => api);

import { AttachmentRuleList } from "../../../src/features/email/AttachmentRuleList";

const WORK = "11111111-1111-1111-1111-111111111111";
const HOME = "22222222-2222-2222-2222-222222222222";
const OTHER = "33333333-3333-3333-3333-333333333333";
const client = {} as never;

const RULE = (id: string, sourceId: string) => ({
  id,
  source_id: sourceId,
  match_type: "sender",
  match_value: "billing@acme.com",
  stale: false,
});

function overlapItem(sourceId: string, over: Record<string, unknown> = {}) {
  return {
    source_id: sourceId,
    source_name: "Work",
    rule_count: 1,
    overlap: true,
    confidence: "high",
    status: "ok",
    message: "Paperless is also watching this mailbox.",
    ...over,
  };
}

/** One provider shared by every child, exactly as the app has. A fresh client
 *  per render would defeat the deduplication under test — which is itself worth
 *  saying, because the tempting "isolate each test" instinct hides the bug. */
function renderLists(sourceIds: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      {sourceIds.map((id) => (
        <AttachmentRuleList key={id} client={client} sourceId={id} />
      ))}
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchAttachmentRules.mockImplementation(async (_c: unknown, sourceId: string) => ({
    rules: [RULE(`r-${sourceId}`, sourceId)],
  }));
  api.fetchPaperlessOverlapAll.mockResolvedValue([]);
});

describe("LUM-836 — the overlap line on the rules page", () => {
  it("shows the message for this mailbox when paperless also watches it", async () => {
    api.fetchPaperlessOverlapAll.mockResolvedValue([overlapItem(WORK)]);

    renderLists([WORK]);

    expect(
      await screen.findByTestId("rules-paperless-overlap"),
    ).toHaveTextContent("Paperless is also watching this mailbox.");
  });

  it("shows nothing on a mailbox with no entry in the batch", async () => {
    // Another mailbox overlaps; this one does not. Selecting the wrong entry is
    // the obvious bug in a shared-key design, so it gets its own test.
    api.fetchPaperlessOverlapAll.mockResolvedValue([overlapItem(OTHER)]);

    renderLists([WORK]);

    await screen.findByTestId("rules-list");
    expect(screen.queryByTestId("rules-paperless-overlap")).toBeNull();
  });

  it("renders nothing when the probe could not tell", async () => {
    api.fetchPaperlessOverlapAll.mockResolvedValue([
      overlapItem(WORK, { overlap: false, confidence: null, status: "unavailable", message: null }),
    ]);

    renderLists([WORK]);

    await screen.findByTestId("rules-list");
    expect(screen.queryByTestId("rules-paperless-overlap")).toBeNull();
  });

  it("renders nothing when the request itself fails", async () => {
    api.fetchPaperlessOverlapAll.mockRejectedValue(new Error("down"));

    renderLists([WORK]);

    await screen.findByTestId("rules-list");
    await waitFor(() => expect(api.fetchPaperlessOverlapAll).toHaveBeenCalled());
    expect(screen.queryByTestId("rules-paperless-overlap")).toBeNull();
  });
});

describe("LUM-836 — what it must NOT do", () => {
  it("issues ONE request for three mounted mailboxes", async () => {
    api.fetchPaperlessOverlapAll.mockResolvedValue([overlapItem(WORK)]);

    renderLists([WORK, HOME, OTHER]);

    await waitFor(() => expect(api.fetchPaperlessOverlapAll).toHaveBeenCalled());
    await waitFor(() => expect(screen.getAllByTestId("rules-list")).toHaveLength(3));
    expect(
      api.fetchPaperlessOverlapAll,
      "a per-source query key would make this 3 — one paperless config fetch per mailbox",
    ).toHaveBeenCalledTimes(1);
  });

  it("issues no request at all when the mailbox has no rules", async () => {
    api.fetchAttachmentRules.mockResolvedValue({ rules: [] });

    renderLists([WORK]);

    await screen.findByTestId("rules-empty");
    expect(api.fetchPaperlessOverlapAll).not.toHaveBeenCalled();
  });

  it("asks for the present-tense copy", async () => {
    // The server owns the wording; the client's only job is to ask for the right
    // tense. Every `_EXISTING` constant shipped once with no caller passing
    // this, leaving them dead while the page rendered "A rule here means…" at
    // someone who was not creating a rule.
    api.fetchPaperlessOverlapAll.mockResolvedValue([overlapItem(WORK)]);

    renderLists([WORK]);

    await waitFor(() => expect(api.fetchPaperlessOverlapAll).toHaveBeenCalled());
    expect(api.fetchPaperlessOverlapAll).toHaveBeenCalledWith(client, "existing");
  });
});
