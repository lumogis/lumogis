// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-819 — the search surface.
//
// The load-bearing thing here is the per-field counts. Subject and sender match
// on substrings while message text matches whole words, and a user cannot see
// which engine answered. Search fails silently — an empty list is
// indistinguishable from an absent message — so "Message 0" beside "Subject 3"
// is what makes the difference legible without any help text. If these tests
// only checked that results render, the feature's actual purpose would be
// untested.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmailSearchBar } from "../../../src/features/email/EmailSearchBar";
import type { EmailSearchHit, EmailSearchResponse } from "../../../src/api/emailSearch";

function hit(over: Partial<EmailSearchHit> = {}): EmailSearchHit {
  return {
    signal_id: "s1",
    source_id: "src-1",
    title: "Plumbing invoice",
    url: "imap://src-1/100/1",
    category: "",
    category_label: "",
    scope: "personal",
    published_at: null,
    content_summary: "",
    matched_in: ["subject"],
    snippet: "",
    ...over,
  } as EmailSearchHit;
}

function response(over: Partial<EmailSearchResponse> = {}): EmailSearchResponse {
  return {
    hits: [hit()],
    counts: { all: 1, subject: 1, sender: 0, body: 0, capped: false },
    fields: "all",
    truncated: false,
    searched_from: null,
    ...over,
  };
}

function clientReturning(res: EmailSearchResponse) {
  return { getJson: vi.fn().mockResolvedValue(res) } as never;
}

const renderHit = (h: EmailSearchHit) => <span>{h.title}</span>;

async function search(client: never, term = "plumbing") {
  fireEvent.change(screen.getByLabelText("Search mail"), { target: { value: term } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect((client as never as { getJson: unknown }).getJson).toHaveBeenCalled());
}

describe("EmailSearchBar", () => {
  it("shows a match count for every field, including the ones that matched nothing", async () => {
    const client = clientReturning(response());
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client);

    // "Message 0" is the point: it explains why a body search came back empty
    // instead of leaving the user to conclude search is broken.
    await waitFor(() => expect(screen.getByRole("button", { name: /Message 0/ })).toBeTruthy());
    expect(screen.getByRole("button", { name: /Subject 1/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Sender 0/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /All 1/ })).toBeTruthy();
  });

  it("renders a capped count as 200+ rather than a number it did not finish counting", async () => {
    const client = clientReturning(
      response({ counts: { all: 200, subject: 200, sender: 0, body: 12, capped: true } }),
    );
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client);

    await waitFor(() => expect(screen.getByRole("button", { name: /All 200\+/ })).toBeTruthy());
  });

  it("narrows to one field and re-runs the search", async () => {
    const client = clientReturning(response());
    render(<EmailSearchBar client={client} renderHit={renderHit} />);
    await search(client);

    fireEvent.click(screen.getByRole("button", { name: /Message 0/ }));

    await waitFor(() =>
      expect((client as never as { getJson: ReturnType<typeof vi.fn> }).getJson).toHaveBeenLastCalledWith(
        expect.stringContaining("fields=body"),
      ),
    );
  });

  it("refuses to search below the minimum length", () => {
    const client = clientReturning(response());
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    fireEvent.change(screen.getByLabelText("Search mail"), { target: { value: "ab" } });

    expect(screen.getByRole("button", { name: "Search" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/at least 3 characters/)).toBeTruthy();
  });

  it("marks a summary-only match as Lumogis's paraphrase, not the sender's words", async () => {
    const client = clientReturning(
      response({ hits: [hit({ matched_in: ["summary"] })] }),
    );
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client);

    await waitFor(() => expect(screen.getByText(/Matched Lumogis's summary/)).toBeTruthy());
  });

  it("shows a body snippet only for a body match", async () => {
    const client = clientReturning(
      response({
        hits: [hit({ matched_in: ["body"], snippet: "…the plumbing work totalling…" })],
      }),
    );
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client);

    await waitFor(() => expect(screen.getByText(/the plumbing work totalling/)).toBeTruthy());
  });

  it("says a truncated message was indexed in part, so a miss is attributable", async () => {
    const client = clientReturning(
      response({
        hits: [hit({ matched_in: ["body"], snippet: "…x…", body_truncated: true })],
      }),
    );
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client);

    await waitFor(() => expect(screen.getByText(/indexed in part/)).toBeTruthy());
  });

  it("explains an empty body-only result rather than just saying no matches", async () => {
    const client = clientReturning(
      response({
        hits: [],
        fields: "body",
        counts: { all: 0, subject: 0, sender: 0, body: 0, capped: false },
      }),
    );
    render(<EmailSearchBar client={client} renderHit={renderHit} />);
    await search(client);

    fireEvent.click(await screen.findByRole("button", { name: /Message 0/ }));

    await waitFor(() => expect(screen.getByText(/full ingest/)).toBeTruthy());
  });

  it("tells the user how far back Lumogis actually holds mail", async () => {
    const client = clientReturning(
      response({
        hits: [],
        counts: { all: 0, subject: 0, sender: 0, body: 0, capped: false },
        searched_from: "2026-03-01T00:00:00Z",
      }),
    );
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client);

    await waitFor(() => expect(screen.getByText(/2026-03-01/)).toBeTruthy());
  });

  it("collapses an owner's shared projection so their own mail is not listed twice", async () => {
    const client = clientReturning(
      response({
        hits: [hit(), hit({ signal_id: "s2", published_from: "s1" } as Partial<EmailSearchHit>)],
        counts: { all: 2, subject: 2, sender: 0, body: 0, capped: false },
      }),
    );
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client);

    await waitFor(() => expect(screen.getAllByText("Plumbing invoice")).toHaveLength(1));
  });

  it("never echoes the query back in an error message", async () => {
    const client = { getJson: vi.fn().mockRejectedValue(new Error("boom")) } as never;
    render(<EmailSearchBar client={client} renderHit={renderHit} />);

    await search(client, "my private search");

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByText(/my private search/)).toBeNull();
  });
});
