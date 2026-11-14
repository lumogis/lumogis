// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-837 chunk A — the sources list: what it shows, what it hides, and the
// two things it must never do (assert health it does not have, render a raw
// exception string).
//
// LUM-844 pause/resume: no Playwright case. The scheduler effect is
// server-computed and pinned in `orchestrator/tests/test_source_pause.py`; the
// client half is here.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { SourcesView } from "../../../src/features/me/SourcesView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { LocationProbe } from "../../helpers/locationProbe";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("admin");
});

function sourceRow(over: Record<string, unknown> = {}) {
  return {
    id: "src-1",
    name: "Household archive",
    source_type: "paperless",
    url: "https://paperless.example.com",
    category: "general",
    active: true,
    poll_interval: 3600,
    last_polled_at: "2026-08-18T09:00:00Z",
    last_signal_at: null,
    polling_active: true,
    ...over,
  };
}

function setup(
  opts: {
    sources?: unknown[];
    deleteResponse?: Response;
    syncResponse?: Response;
    patchResponse?: Response;
    /** LUM-282: lets a test drive the `q.isError` branch. */
    sourcesResponse?: Response;
  } = {},
) {
  const user = { id: "admin", email: "admin@home.lan", role: "admin" as const };
  const calls: { url: string; method?: string }[] = [];
  const patchBodies: Record<string, unknown>[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method });
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
    if (url.includes("/sources/") && init?.method === "PATCH") {
      patchBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return opts.patchResponse ?? jsonResponse(200, { status: "updated", source_id: "src-1" });
    }
    if (url.includes("/sources/") && init?.method === "DELETE") {
      return opts.deleteResponse ?? jsonResponse(200, { status: "deleted", source_id: "src-1" });
    }
    if (url.includes("/sources/") && url.includes("/sync")) {
      return opts.syncResponse ?? jsonResponse(202, { status: "queued", source_id: "src-1" });
    }
    if (url.includes("/sources")) {
      if (opts.sourcesResponse) return opts.sourcesResponse;
      const sources = opts.sources ?? [sourceRow()];
      return jsonResponse(200, { sources, total: sources.length });
    }
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, calls, patchBodies };
}

// LUM-282 added `useLocation()` to SourcesView (it reads the onboarding
// handoff's navigation state to decide whether to take focus), so the view now
// requires a Router in every test that renders it.
function renderView(s: ReturnType<typeof setup>, opts: { fromOnboarding?: boolean } = {}) {
  render(
    <AuthProvider client={s.client} tokens={s.tokens} skipRefreshOnMount>
      <MemoryRouter
        initialEntries={[
          opts.fromOnboarding
            ? { pathname: "/me/sources", state: { fromOnboarding: true } }
            : { pathname: "/me/sources" },
        ]}
      >
        <LocationProbe stateKeys={["fromOnboarding"]} />
        <SourcesView />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("SourcesView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists name, type and category", async () => {
    const s = setup();
    renderView(s);
    await screen.findByText("Household archive");
    expect(screen.getByText("paperless")).toBeInTheDocument();
    expect(screen.getByText("general")).toBeInTheDocument();
  });

  it("does not show mailboxes — they are managed on the email page", async () => {
    const s = setup({
      sources: [
        sourceRow(),
        sourceRow({ id: "src-imap", name: "Personal mail", source_type: "imap" }),
      ],
    });
    renderView(s);
    await screen.findByText("Household archive");
    expect(screen.queryByText("Personal mail")).not.toBeInTheDocument();
  });

  it("shows a paused source as paused, not as failing", async () => {
    // `_record_poll_health` clears an error only on a SUCCESSFUL poll, and a
    // paused source never polls again — so an error-first precedence would
    // leave this row shouting `role="alert"` forever, and pausing a broken
    // source (the obvious way to silence it) would not work.
    const s = setup({
      sources: [sourceRow({ active: false, last_poll_error_code: "credential" })],
    });
    renderView(s);
    expect(await screen.findByText(/^Paused/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a failing source rather than the time it last tried", async () => {
    // `last_polled_at` alone is not health: the paperless poller advances the
    // timestamp even when the credential is missing, so a fresh timestamp on a
    // dead source would otherwise read as "working".
    const s = setup({
      sources: [sourceRow({ last_poll_error_code: "credential" })],
    });
    renderView(s);
    const alert = await screen.findByRole("alert");
    // "credential" is the code `_poll_paperless_source` writes when
    // `pwc.load_connection` fails — a string that exists in production,
    // not one invented for the test.
    expect(alert.textContent).toContain("credential");
    expect(screen.queryByText(/Last polled/)).not.toBeInTheDocument();
  });

  it("removing asks for confirmation first", async () => {
    const s = setup();
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Remove Household archive$/ }));
    expect(
      screen.getByRole("button", { name: /^Confirm remove Household archive$/ }),
    ).toBeInTheDocument();
    // Nothing was sent by merely asking.
    expect(s.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("confirming removes the source", async () => {
    const s = setup();
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Remove Household archive$/ }));
    await user.click(screen.getByRole("button", { name: /^Confirm remove Household archive$/ }));
    await waitFor(() => expect(s.calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(await screen.findByRole("status")).toHaveTextContent("Source removed.");
  });

  it("never renders the server's exception string", async () => {
    // The 500 body is `Could not save source: {exc}` — an exception string that
    // can carry a DSN or a host.
    const s = setup({
      deleteResponse: jsonResponse(500, {
        detail: "Could not save source: postgres://user:pw@db.internal/lumogis",
      }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Remove Household archive$/ }));
    await user.click(screen.getByRole("button", { name: /^Confirm remove Household archive$/ }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Something went wrong — try again.");
    expect(document.body.textContent).not.toContain("postgres://");
    expect(document.body.textContent).not.toContain("db.internal");
  });

  it("says a sync is already queued rather than apologising, on 409", async () => {
    const s = setup({ syncResponse: jsonResponse(409, { detail: { code: "already_running" } }) });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Sync now$/ }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "A sync is already queued for that source.",
    );
  });

  it("offers Sync now per row", async () => {
    const s = setup();
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Sync now$/ }));
    await waitFor(() => expect(s.calls.some((c) => c.url.includes("/sync"))).toBe(true));
  });

  // --- LUM-844: pause / resume ------------------------------------------------

  it("offers Pause on an active row and Resume on a paused one", async () => {
    const s = setup({
      sources: [
        sourceRow({ id: "a", name: "Household archive" }),
        sourceRow({ id: "b", name: "Weather feed", source_type: "rss", active: false }),
      ],
    });
    renderView(s);
    expect(await screen.findByRole("button", { name: "Pause Household archive" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume Weather feed" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause Weather feed" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume Household archive" })).not.toBeInTheDocument();
  });

  it("pausing sends only active:false", async () => {
    const s = setup({
      patchResponse: jsonResponse(200, {
        status: "updated",
        source_id: "src-1",
        active: false,
        rescheduled: false,
        schedule_synced: true,
      }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: "Pause Household archive" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Source paused. A check already in progress will finish; nothing new is checked until you resume it.",
    );
    expect(s.patchBodies).toEqual([{ active: false }]);
  });

  it("resuming sends only active:true", async () => {
    // Kills the mutation that always sends `active: false` regardless of the row.
    const s = setup({
      sources: [sourceRow({ active: false })],
      patchResponse: jsonResponse(200, {
        status: "updated",
        source_id: "src-1",
        active: true,
        rescheduled: true,
        schedule_synced: true,
      }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: "Resume Household archive" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Source resumed. Checking it now.");
    expect(s.patchBodies).toEqual([{ active: true }]);
  });

  it("hides Sync now on a paused row", async () => {
    // The sync route answers 409 source_paused for it, so the button is a dead end.
    const s = setup({ sources: [sourceRow({ active: false })] });
    renderView(s);
    await screen.findByText("Household archive");
    expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
  });

  it("says the source is paused when sync is refused for that reason", async () => {
    // A stale list can still show Sync now for a row paused elsewhere. Keyed on
    // `code`, because a 409 also means "already queued".
    const s = setup({ syncResponse: jsonResponse(409, { detail: { code: "source_paused" } }) });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Sync now$/ }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "That source is paused — resume it to sync.",
    );
    expect(document.body.textContent).not.toContain("already queued");
  });

  it("a failed pause never renders the server's detail", async () => {
    const s = setup({
      patchResponse: jsonResponse(500, {
        detail: "Could not save source: postgres://u:p@db/x",
      }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: "Pause Household archive" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Something went wrong — try again.",
    );
    expect(document.body.textContent).not.toContain("postgres://");
  });

  it("a resume whose schedule did not take says so, and Try again re-sends it", async () => {
    const s = setup({
      sources: [sourceRow({ active: false })],
      patchResponse: jsonResponse(200, {
        status: "updated",
        source_id: "src-1",
        active: true,
        rescheduled: false,
        schedule_synced: false,
      }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: "Resume Household archive" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Saved as resumed, but checking could not be restarted yet.",
    );
    expect(document.body.textContent).not.toContain("Checking it now.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(s.patchBodies).toHaveLength(2));
    // The list is static in this fixture, so the row still reads paused. What
    // matters is that the retry re-sends the SAME value, not the row's inverse.
    expect(s.patchBodies).toEqual([{ active: true }, { active: true }]);
  });

  it("words the result from the state the server reports, not from the click", async () => {
    // Pause clicked, but another tab resumed first: the server answers with
    // `active: true`. Saying "Source paused" there would be false.
    const s = setup({
      patchResponse: jsonResponse(200, {
        status: "unchanged",
        source_id: "src-1",
        active: true,
        rescheduled: true,
        schedule_synced: true,
      }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: "Pause Household archive" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Source resumed. Checking it now.");
    expect(document.body.textContent).not.toContain("Source paused");
  });

  it("a pause whose schedule did not take says so", async () => {
    const s = setup({
      patchResponse: jsonResponse(200, {
        status: "updated",
        source_id: "src-1",
        active: false,
        rescheduled: false,
        schedule_synced: false,
      }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: "Pause Household archive" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Saved as paused, but Lumogis could not confirm it stopped checking.",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("editing a row sends name, category and interval — and never url", async () => {
    // `url` is not on the edit form by design: changing it is
    // delete-and-recreate, since the poll cursor, the detected type and the
    // extraction method all derive from the original.
    const s = setup({ patchResponse: jsonResponse(200, { status: "updated", source_id: "src-1" }) });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Edit Household archive$/ }));
    const nameInput = screen.getByLabelText("Name for Household archive");
    await user.clear(nameInput);
    await user.type(nameInput, "Archive");
    await user.click(screen.getByRole("button", { name: /^Save Household archive$/ }));
    await waitFor(() => expect(s.patchBodies).toHaveLength(1));
    expect(s.patchBodies[0]).toMatchObject({ name: "Archive", category: "general" });
    expect(s.patchBodies[0].url).toBeUndefined();
    expect(await screen.findByRole("status")).toHaveTextContent("Source updated.");
  });

  it("says 'no changes' rather than 'saved' when the route reports unchanged", async () => {
    // `unchanged` means the values supplied already matched, so claiming a save
    // would assert a write that did not happen.
    const s = setup({
      patchResponse: jsonResponse(200, { status: "unchanged", source_id: "src-1" }),
    });
    const user = userEvent.setup();
    renderView(s);
    await user.click(await screen.findByRole("button", { name: /^Edit Household archive$/ }));
    await user.click(screen.getByRole("button", { name: /^Save Household archive$/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("No changes to save.");
  });

  // LUM-282 replaces this test's predecessor, which was named "says nothing
  // rather than showing an empty table when there are no sources" and asserted
  // "No sources yet.". That name recorded a deliberate choice; this ticket is
  // the decision to say something instead, so the name changes with the
  // assertion. Keeping the old name over a new assertion would leave a test
  // whose title lies about what it guards.
  it("offers document setup instead of an empty table when there are no sources", async () => {
    const s = setup({ sources: [] });
    renderView(s);

    // Assert the OFFER, not merely that something rendered. "Some text is
    // present" would pass against an EmptyState with no copy in it, which is
    // the exact failure this chunk exists to remove.
    expect(
      await screen.findByRole("heading", {
        name: "No document archives, feeds or watched pages yet",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Paperless-ngx archive/)).toBeInTheDocument();
    expect(screen.getByText(/save its address and API token first/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("No sources yet.")).not.toBeInTheDocument();
  });

  // The trap this guards: `rows` is filtered by MANAGED_TYPES, so the empty
  // branch is ALSO reached by someone with mailboxes. Copy claiming the account
  // is empty would be false for them.
  it("does not claim the account is empty when the user has mailboxes", async () => {
    const s = setup({ sources: [sourceRow({ id: "m-1", source_type: "imap" })] });
    renderView(s);
    await screen.findByRole("heading", {
      name: "No document archives, feeds or watched pages yet",
    });
    // Nothing on screen asserts the user has connected nothing.
    expect(screen.queryByText(/haven't connected anything/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/nothing connected/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no sources/i)).not.toBeInTheDocument();
  });

  it("drops the offer once a managed source exists", async () => {
    const s = setup({ sources: [sourceRow()] });
    renderView(s);
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", {
        name: "No document archives, feeds or watched pages yet",
      }),
    ).not.toBeInTheDocument();
  });

  // Guards the deliberate outline decision: two sibling h2s under one h1, in
  // the order "what's missing" then "how to add one". A later edit that nests
  // or reorders them should fail here.
  it("outlines the empty page as h1 Sources, then the offer, then Add a source", async () => {
    const s = setup({ sources: [] });
    renderView(s);
    await screen.findByRole("heading", {
      name: "No document archives, feeds or watched pages yet",
    });
    const headings = screen.getAllByRole("heading");
    expect(headings.map((h) => [h.tagName, h.textContent])).toEqual([
      ["H1", "Sources"],
      ["H2", "No document archives, feeds or watched pages yet"],
      ["H2", "Add a source"],
    ]);
  });

  // LUM-282 focus handling. `ModalFrame` restores focus to the pre-modal
  // element, which after a cross-route close is disconnected — so without this
  // the handoff lands a keyboard user on <body>.
  it("takes focus to the heading when arrived from the onboarding handoff", async () => {
    const s = setup({ sources: [] });
    renderView(s, { fromOnboarding: true });
    const h1 = await screen.findByRole("heading", { level: 1, name: "Sources" });
    expect(document.activeElement).toBe(h1);
    expect(document.activeElement).not.toBe(document.body);
  });

  // Reproduces the pending→loaded race: first focus fails while #root is inert,
  // sources resolve before the macrotask retry, and the loaded-branch heading
  // must still receive focus (Chromium enforces inert; jsdom does not).
  it("focuses the loaded heading when the first attempt fails due to inert", async () => {
    const root = document.createElement("div");
    root.id = "root";
    root.setAttribute("inert", "");
    document.body.appendChild(root);

    const origFocus = HTMLElement.prototype.focus;
    const focusSpy = vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (
      this: HTMLElement,
      ...args: Parameters<HTMLElement["focus"]>
    ) {
      if (root.hasAttribute("inert")) return;
      origFocus.call(this, ...args);
    });

    let resolveSources!: (v: Response) => void;
    const sourcesPromise = new Promise<Response>((resolve) => {
      resolveSources = resolve;
    });

    const user = { id: "admin", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (url.includes("/sources") && init?.method !== "PATCH" && init?.method !== "DELETE") {
        return sourcesPromise;
      }
      return jsonResponse(404, { detail: "not found" });
    });
    const tokens = new AccessTokenStore();
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <MemoryRouter
          initialEntries={[{ pathname: "/me/sources", state: { fromOnboarding: true } }]}
        >
          <SourcesView />
        </MemoryRouter>
      </AuthProvider>,
      { container: root },
    );

    await screen.findByText("Loading…");
    await new Promise((r) => setTimeout(r, 0));

    root.removeAttribute("inert");
    resolveSources!(jsonResponse(200, { sources: [], total: 0 }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Add a source" })).toBeInTheDocument();
    });
    await waitFor(() => {
      const h1 = screen.getByRole("heading", { level: 1, name: "Sources" });
      expect(document.activeElement).toBe(h1);
    });

    focusSpy.mockRestore();
    root.remove();
  });

  // §7a re-review finding: `location.state` belongs to the HISTORY ENTRY, not
  // to the visit. Left set, every later return to this entry — Back, forward, a
  // restored session — would remount with a fresh focus latch and steal focus
  // again on an arrival the user did not start from onboarding.
  it("scrubs the onboarding flag from history state once it has been used", async () => {
    const s = setup({ sources: [] });
    renderView(s, { fromOnboarding: true });
    await screen.findByRole("heading", { level: 1, name: "Sources" });
    await waitFor(() => {
      expect(screen.getByTestId("location-probe").dataset.fromOnboarding).toBe("false");
    });
  });

  it("does not steal focus on an ordinary visit", async () => {
    const s = setup({ sources: [] });
    renderView(s);
    await screen.findByRole("heading", { level: 1, name: "Sources" });
    expect(document.activeElement).toBe(document.body);
  });

  // The error branch must NOT fall through to the offer. "We couldn't load your
  // sources" and "you have no sources" are opposite statements, and rendering
  // the second for the first is the same category of untruth as the defect this
  // chunk removes.
  it("shows the error, not the offer, when the list cannot be loaded", async () => {
    const s = setup({ sourcesResponse: jsonResponse(500, { detail: "boom" }) });
    renderView(s);
    expect(await screen.findByText("Sources unavailable.")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", {
        name: "No document archives, feeds or watched pages yet",
      }),
    ).not.toBeInTheDocument();
  });
});
