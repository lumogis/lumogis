// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-697 — the sender-grouped Subscriptions surface.
//
// The load-bearing one is the render-side scheme gate: the server already
// rejects a `javascript:` URI at parse, but a row stored before that fix (or
// restored from a backup) would still be served, and turning it into an <a
// href> is precisely the published stored-XSS technique (CVE-2025-68673).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../../../src/api/client";
import { isSafeUnsubscribeUrl } from "../../../src/api/emailUnsubscribe";
import { MAX_BATCH, SubscriptionsView } from "../../../src/features/email/SubscriptionsView";

const SRC = "src-1";

function row(over: Partial<Record<string, unknown>> = {}) {
  return {
    source_id: SRC,
    from_addr: "news@retailer.example",
    from_display: "Retailer",
    message_count: 41,
    has_mailto: true,
    has_https: false,
    one_click: false,
    https_uri: "",
    last_seen_at: null,
    unsubscribed_at: null,
    suppressed_as: null,
    ...over,
  };
}

function makeClient(
  rows: unknown[],
  overrides: Record<string, unknown> = {},
  jobs: unknown[] = [],
) {
  // Keep the mock's own type so assertions on .mock.calls stay checked; the cast
  // to ApiClient happens at the prop site only.
  return {
    // LUM-710 added a second GET (the batch-progress poll). Answering it with
    // its real shape rather than letting it fall through to the rows response
    // keeps these tests exercising the poll's success path.
    getJson: vi.fn(async (path: string) =>
      path.startsWith("/api/v1/email/unsubscribe/jobs") ? { jobs } : { rows, truncated: false },
    ),
    postJson: vi.fn(async (_path: string, _body: unknown) => ({
      proposal_id: 7,
      targets: [{}],
      skipped: [] as string[],
    })),
    delete: vi.fn(async (_path: string) => undefined),
    ...overrides,
  };
}

type FakeClient = ReturnType<typeof makeClient>;

function view(client: FakeClient) {
  return <SubscriptionsView client={client as unknown as ApiClient} sourceId={SRC} />;
}

// --------------------------------------------------------------------------- //
// the render-side scheme gate
// --------------------------------------------------------------------------- //
describe("isSafeUnsubscribeUrl", () => {
  it("accepts a plain https URI", () => {
    expect(isSafeUnsubscribeUrl("https://x.com/u?t=1")).toBe(true);
  });

  it("rejects javascript:, data:, file: and http:", () => {
    for (const bad of [
      "javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "file:///etc/passwd",
      "http://x.com/u",
    ]) {
      expect(isSafeUnsubscribeUrl(bad)).toBe(false);
    }
  });

  it("rejects a URI carrying userinfo", () => {
    // Displays as real-bank.example in some UIs while resolving to evil.
    expect(isSafeUnsubscribeUrl("https://evil@real-bank.example/u")).toBe(false);
    expect(isSafeUnsubscribeUrl("https://u:p@x.com/u")).toBe(false);
  });

  it("rejects unparseable input rather than throwing", () => {
    expect(isSafeUnsubscribeUrl("))((")).toBe(false);
    expect(isSafeUnsubscribeUrl("")).toBe(false);
  });
});

describe("SubscriptionsView", () => {
  it("renders senders with their message counts", async () => {
    render(view(makeClient([row()])));
    expect(await screen.findByText("Retailer")).toBeInTheDocument();
    expect(screen.getByText("41 messages")).toBeInTheDocument();
  });

  it("renders a safe https URI as a target=_blank link with rel=noopener noreferrer", async () => {
    render(view(makeClient([row({ has_https: true, https_uri: "https://x.com/u" })])));
    const link = await screen.findByTestId("unsubscribe-link");
    expect(link).toHaveAttribute("href", "https://x.com/u");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });

  it("refuses to render a non-https stored URI as a link", async () => {
    // A row that predates the parser fix must not become a live javascript: link.
    render(view(makeClient([row({ has_https: true, https_uri: "javascript:alert(1)" })])));
    await screen.findByTestId("subscription-row");
    expect(screen.queryByTestId("unsubscribe-link")).toBeNull();
  });

  // ------------------------------------------------------------------------- //
  // batching
  // ------------------------------------------------------------------------- //
  it("builds ONE batch request for the whole selection, not one per sender", async () => {
    const client = makeClient([
      row({ from_addr: "a@x.com", from_display: "A" }),
      row({ from_addr: "b@x.com", from_display: "B" }),
    ]);
    render(view(client));
    await screen.findByText("A");
    fireEvent.click(screen.getByLabelText("Select a@x.com"));
    fireEvent.click(screen.getByLabelText("Select b@x.com"));
    fireEvent.click(screen.getByRole("button", { name: /Unsubscribe from 2 selected/ }));

    await waitFor(() => expect(client.postJson).toHaveBeenCalledTimes(1));
    const [path, body] = client.postJson.mock.calls[0];
    expect(path).toBe("/api/v1/email/unsubscribe/propose");
    expect(body).toEqual({ source_id: SRC, senders: ["a@x.com", "b@x.com"] });
  });

  it("never sends a recipient address to the server — senders only", async () => {
    const client = makeClient([row({ from_addr: "a@x.com", from_display: "A" })]);
    render(view(client));
    await screen.findByText("A");
    fireEvent.click(screen.getByLabelText("Select a@x.com"));
    fireEvent.click(screen.getByRole("button", { name: /Unsubscribe from 1 selected/ }));
    await waitFor(() => expect(client.postJson).toHaveBeenCalled());
    expect(JSON.stringify(client.postJson.mock.calls[0][1])).not.toContain("mailto");
  });

  it("cannot select a sender with no mailto form", async () => {
    render(
      view(makeClient([row({ has_mailto: false, has_https: true, https_uri: "https://x.com/u" })])),
    );
    const box = await screen.findByRole("checkbox", { name: "Select news@retailer.example" });
    expect(box).toHaveAttribute("aria-disabled", "true");
  });

  it("caps the selection at the batch cap and says so", async () => {
    // Derived, not hardcoded: LUM-710 moved execution off the request thread and
    // raised the cap, and a literal here would have kept passing at the old
    // value while the feature was silently capped at ten.
    const over = MAX_BATCH + 2;
    const rows = Array.from({ length: over }, (_, i) =>
      row({ from_addr: `a${i}@x.com`, from_display: `A${i}` }),
    );
    render(view(makeClient(rows)));
    await screen.findByText("A0");
    for (let i = 0; i < over; i += 1) {
      fireEvent.click(screen.getByLabelText(`Select a${i}@x.com`));
    }
    expect(
      screen.getByRole("button", { name: new RegExp(`Unsubscribe from ${MAX_BATCH} selected`) }),
    ).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`${MAX_BATCH} at a time`))).toBeInTheDocument();
  });

  it("says the batch went for approval, not that it was sent", async () => {
    const client = makeClient([row({ from_addr: "a@x.com", from_display: "A" })]);
    render(view(client));
    await screen.findByText("A");
    fireEvent.click(screen.getByLabelText("Select a@x.com"));
    fireEvent.click(screen.getByRole("button", { name: /Unsubscribe from 1 selected/ }));
    // Nothing sends until the user approves — the copy must not imply otherwise.
    expect(await screen.findByText(/for approval/)).toBeInTheDocument();
  });

  // ------------------------------------------------------------------------- //
  // suppression
  // ------------------------------------------------------------------------- //
  it("offers Hide future mail, and Stop hiding once a rule exists", async () => {
    const { rerender } = render(view(makeClient([row()])));
    expect(await screen.findByRole("button", { name: "Hide future mail" })).toBeInTheDocument();

    rerender(view(makeClient([row({ suppressed_as: "hide" })])));
    // "Stop hiding", never "Restore" — clearing a rule is go-forward only.
    expect(await screen.findByRole("button", { name: "Stop hiding" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /restore/i })).toBeNull();
  });

  it("shows an empty state rather than a blank panel", async () => {
    render(view(makeClient([])));
    expect(await screen.findByText(/No subscriptions found yet/)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// LUM-710 — the list must stay live while a batch drains off-thread
// --------------------------------------------------------------------------- //
//
// Before LUM-710 an approved batch was finished by the time the approval call
// returned, so a static list was accurate. Now it drains on the worker over the
// following minutes, and each row's own "Unsubscribed ✓" only tells the truth if
// the list refreshes while that happens. These pin the refresh — and pin that it
// STOPS, because a poll loop that never ends is a background request every five
// seconds for as long as the tab is open.
describe("SubscriptionsView batch progress polling", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  const running = [
    { job_id: 1, status: "running", total: 2, enqueued_at: null, finished_at: null },
  ];

  it("does not poll when no batch is in flight", async () => {
    const client = makeClient([row({ from_display: "A" })], {}, []);
    render(view(client));
    await screen.findByText("A");
    const after = client.getJson.mock.calls.length;

    await vi.advanceTimersByTimeAsync(20_000);

    expect(client.getJson.mock.calls.length).toBe(after);
    expect(screen.queryByTestId("unsubscribe-batch-running")).toBeNull();
  });

  it("refreshes the list while a batch drains, so rows flip as each send lands", async () => {
    let rows = [row({ from_addr: "a@x.com", from_display: "A" })];
    const client = {
      getJson: vi.fn(async (path: string) =>
        path.startsWith("/api/v1/email/unsubscribe/jobs")
          ? { jobs: running }
          : { rows, truncated: false },
      ),
      postJson: vi.fn(),
      delete: vi.fn(),
    };
    render(view(client as unknown as FakeClient));
    await screen.findByTestId("unsubscribe-batch-running");
    expect(screen.queryByText("Unsubscribed ✓")).toBeNull();

    // The worker sends the first target and stamps it.
    rows = [
      row({ from_addr: "a@x.com", from_display: "A", unsubscribed_at: "2026-08-08T00:00:00Z" }),
    ];
    await vi.advanceTimersByTimeAsync(6000);

    await waitFor(() => expect(screen.getByText("Unsubscribed ✓")).toBeInTheDocument());
  });

  it("stops polling once the batch finishes", async () => {
    let jobs: unknown[] = running;
    const client = {
      getJson: vi.fn(async (path: string) =>
        path.startsWith("/api/v1/email/unsubscribe/jobs")
          ? { jobs }
          : { rows: [row({ from_display: "A" })], truncated: false },
      ),
      postJson: vi.fn(),
      delete: vi.fn(),
    };
    render(view(client as unknown as FakeClient));
    await screen.findByTestId("unsubscribe-batch-running");

    jobs = [{ job_id: 1, status: "done", total: 2, enqueued_at: null, finished_at: "t" }];
    await vi.advanceTimersByTimeAsync(6000);
    await waitFor(() => expect(screen.queryByTestId("unsubscribe-batch-running")).toBeNull());
    const settled = client.getJson.mock.calls.length;

    await vi.advanceTimersByTimeAsync(30_000);

    expect(client.getJson.mock.calls.length).toBe(settled);
  });

  it("stops polling when the jobs endpoint fails rather than retrying forever", async () => {
    const client = {
      getJson: vi.fn(async (path: string) => {
        if (path.startsWith("/api/v1/email/unsubscribe/jobs")) throw new Error("boom");
        return { rows: [row({ from_display: "A" })], truncated: false };
      }),
      postJson: vi.fn(),
      delete: vi.fn(),
    };
    render(view(client as unknown as FakeClient));
    await screen.findByText("A");
    const after = client.getJson.mock.calls.length;

    await vi.advanceTimersByTimeAsync(30_000);

    expect(client.getJson.mock.calls.length).toBe(after);
  });
});
