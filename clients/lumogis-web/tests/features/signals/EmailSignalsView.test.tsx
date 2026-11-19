// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { toast } from "sonner";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { EmailSignalsView } from "../../../src/features/signals/EmailSignalsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// Success messages are sonner toasts (LUM-993); there is no Toaster in this tree.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

const TAXONOMY = {
  categories: [
    { slug: "school", label: "School", enabled: true, is_custom: false, is_protected: false, sort: 0 },
    { slug: "other", label: "Other", enabled: true, is_custom: false, is_protected: true, sort: 1 },
  ],
};

const SIGNALS = {
  signals: [
    {
      signal_id: "s1",
      source_id: "src-1",
      title: "Field trip",
      url: "imap://src-1/1/42",
      category: "school",
      category_label: "School",
      scope: "personal",
      published_at: null,
      content_summary: "",
    },
    {
      signal_id: "s2",
      source_id: "src-2",
      title: "Shared landlord mail",
      url: "imap://src-2/1/7",
      category: "a_landlord",
      category_label: "Landlord",
      scope: "shared",
      published_at: null,
      content_summary: "",
    },
    {
      signal_id: "s3",
      source_id: "src-1",
      title: "No category",
      url: "imap://src-1/1/99",
      category: "",
      category_label: "",
      scope: "personal",
      published_at: null,
      content_summary: "",
    },
    {
      signal_id: "s4",
      source_id: "src-2",
      title: "Deleted custom",
      url: "imap://src-2/1/8",
      category: "gone_slug",
      category_label: "gone_slug",
      scope: "shared",
      published_at: null,
      content_summary: "",
    },
  ],
  total: 4,
};

const SOURCES = {
  sources: [
    {
      id: "src-1",
      name: "Personal",
      source_type: "imap",
      url: "imap://imap.gmail.com:993/personal",
      category: "email",
      active: true,
      poll_interval: 900,
      last_polled_at: null,
      last_signal_at: null,
      polling_active: true,
    },
  ],
  total: 1,
};

function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  // LUM-849: the view now renders a <Link> to the autonomy page, which needs a
  // router context. Same wrapper `MeConnectorsView.test.tsx` uses for its
  // signpost.
  return render(
    <MemoryRouter>
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailSignalsView />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("EmailSignalsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mockFetch(
    syncSpy?: Mock<(url: string) => void>,
    sources: typeof SOURCES = SOURCES,
    deleteSpy?: Mock<(url: string) => void>,
  ) {
    return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-taxonomy")) return jsonResponse(200, TAXONOMY);
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      if (u.includes("/sync") && init?.method === "POST") {
        syncSpy?.(u);
        return jsonResponse(202, { status: "queued", source_id: "src-1" });
      }
      if (u.includes("/sources/src-1") && init?.method === "DELETE") {
        deleteSpy?.(u);
        return jsonResponse(200, { status: "deleted", source_id: "src-1" });
      }
      if (u.includes("/sources") && !u.includes("/sync")) return jsonResponse(200, sources);
      if (u.includes("/signals")) return jsonResponse(200, SIGNALS);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
  }

  // LUM-993: the sorted-mail list lived here and duplicated the Email page, so
  // this page now only points there and never asks for signals.
  it("links to the Email page instead of listing sorted mail", async () => {
    const urls: string[] = [];
    const base = mockFetch();
    renderView((async (input: RequestInfo | URL, init?: RequestInit) => {
      urls.push(String(input));
      return base(input as RequestInfo, init);
    }) as typeof fetch);
    const link = await screen.findByRole("link", { name: "Email page" });
    expect(link).toHaveAttribute("href", "/email");
    await screen.findByText("Personal");
    expect(screen.queryByText("Field trip")).not.toBeInTheDocument();
    expect(screen.queryByText("No signals yet.")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Category filters" })).not.toBeInTheDocument();
    expect(urls.filter((u) => u.includes("/signals"))).toHaveLength(0);
  });

  it("queues sync now for an IMAP source", async () => {
    const syncSpy = vi.fn();
    renderView(mockFetch(syncSpy));
    await waitFor(() => expect(screen.getByLabelText("Sync now Personal")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Sync now Personal"));
    await waitFor(() => expect(syncSpy).toHaveBeenCalled());
    expect(String(syncSpy.mock.calls[0][0])).toContain("/sources/src-1/sync");
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Sync queued."));
  });

  // The "…" menu holds the mode switch; the card says what is stored either way.
  it("switches the storage mode from the … menu", async () => {
    const patches: unknown[] = [];
    const base = mockFetch();
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes("/sources/src-1") && init?.method === "PATCH") {
        patches.push(JSON.parse(String(init.body)));
        return jsonResponse(200, {
          ...SOURCES.sources[0],
          extraction_method: "imap_full_ingest",
          full_ingest_active: false,
        });
      }
      return base(input, init);
    }) as unknown as typeof fetch;
    renderView(fetchImpl);
    expect(await screen.findByTestId("mode-src-1")).toHaveTextContent("Subject and sender only");
    await userEvent.click(screen.getByRole("button", { name: "More actions for Personal" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Switch to full messages" }));
    await waitFor(() => expect(patches).toEqual([{ full_ingest: true }]));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Switched to full messages (waiting for the household admin to allow it).",
      ),
    );
  });

  // LUM-690/633 — a mailbox that can't currently authenticate (revoked OAuth
  // grant, or a soft-saved OAuth connect whose first real sync then failed) is
  // visible in "Your mailboxes" instead of only logging silently.
  it("shows no health warning for a healthy mailbox", async () => {
    renderView(mockFetch());
    await waitFor(() => expect(screen.getByText("Personal")).toBeInTheDocument());
    expect(screen.queryByTestId("health-src-1")).not.toBeInTheDocument();
  });

  it("shows a health warning when the mailbox can't authenticate", async () => {
    const unhealthySources = {
      sources: [
        {
          ...SOURCES.sources[0],
          last_poll_error_code: "credential",
          last_poll_error_at: "2026-07-24T12:00:00+00:00",
        },
      ],
      total: 1,
    };
    renderView(mockFetch(undefined, unhealthySources));
    const warning = await screen.findByTestId("health-src-1");
    expect(warning).toHaveTextContent(/couldn't sync/i);
    // LUM-633 reworded this per code. `credential` is the one case the user can
    // actually fix, so it is the only one that should mention reconnecting.
    expect(warning).toHaveTextContent(/can't sign in to this mailbox/i);
    expect(warning).toHaveTextContent(/reconnect/i);
  });

  it("passes the mailbox auth method through to the health copy", async () => {
    // LUM-777. The same `config` code names a different owner per auth method.
    const withMethod = (auth_method?: "oauth") => ({
      sources: [
        {
          ...SOURCES.sources[0],
          last_poll_error_code: "config",
          last_poll_error_at: "2026-07-24T12:00:00+00:00",
          ...(auth_method ? { auth_method } : {}),
        },
      ],
      total: 1,
    });

    const first = renderView(mockFetch(undefined, withMethod("oauth")));
    const oauth = await first.findByTestId("health-src-1");
    expect(oauth).toHaveTextContent(/Ask your Lumogis operator/);
    expect(oauth).not.toHaveTextContent(/if you entered them yourself/i);
    first.unmount();

    // An older backend omits the key: unknown keeps the two-owner copy.
    renderView(mockFetch(undefined, withMethod()));
    const unknown = await screen.findByTestId("health-src-1");
    expect(unknown).toHaveTextContent(/if you entered them yourself/i);
  });

  it("does not tell the user to fix credentials when the block is an operator policy", async () => {
    // LUM-633: before the per-code split, an egress-blocked mailbox rendered the
    // credential copy, sending the user to re-enter a password that was fine.
    const blockedSources = {
      sources: [
        {
          ...SOURCES.sources[0],
          last_poll_error_code: "egress_blocked",
          last_poll_error_at: "2026-07-24T12:00:00+00:00",
        },
      ],
      total: 1,
    };
    renderView(mockFetch(undefined, blockedSources));
    const warning = await screen.findByTestId("health-src-1");
    expect(warning).toHaveTextContent(/credentials are fine/i);
    expect(warning).toHaveTextContent(/operator/i);
    expect(warning).not.toHaveTextContent(/reconnect it/i);
  });

  // LUM-701 — self-service mailbox removal.
  async function openRemoveDialog() {
    await screen.findByText("Personal");
    await userEvent.click(screen.getByRole("button", { name: "More actions for Personal" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Remove mailbox" }));
    return screen.findByRole("alertdialog");
  }

  it("removes a mailbox after confirming", async () => {
    const deleteSpy = vi.fn();
    renderView(mockFetch(undefined, SOURCES, deleteSpy));
    const dialog = await openRemoveDialog();
    expect(dialog).toHaveTextContent("Remove Personal?");
    expect(dialog).toHaveTextContent(
      "This stops syncing and disconnects the mailbox. Mail already sorted is kept. You can connect it again at any time.",
    );
    expect(deleteSpy).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove mailbox" }));
    await waitFor(() => expect(deleteSpy).toHaveBeenCalled());
    expect(String(deleteSpy.mock.calls[0][0])).toContain("/sources/src-1");
  });

  it("does not remove a mailbox when the dialog is cancelled", async () => {
    const deleteSpy = vi.fn();
    renderView(mockFetch(undefined, SOURCES, deleteSpy));
    const dialog = await openRemoveDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it("shows an error and keeps the mailbox listed when removal fails", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-taxonomy")) return jsonResponse(200, TAXONOMY);
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      if (u.includes("/sources/src-1") && init?.method === "DELETE") {
        return jsonResponse(500, { detail: "boom" });
      }
      if (u.includes("/sources") && !u.includes("/sync")) return jsonResponse(200, SOURCES);
      if (u.includes("/signals")) return jsonResponse(200, SIGNALS);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    renderView(fetchImpl);
    const dialog = await openRemoveDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove mailbox" }));
    await waitFor(() => expect(screen.getByTestId("delete-msg")).toHaveTextContent(/couldn't remove/i));
    expect(screen.getByText("Personal")).toBeInTheDocument();
  });

  // --- LUM-849: the autonomy signpost ----------------------------------- //
  it("renders the autonomy signpost exactly once with three mailboxes", async () => {
    // The trap this test exists for: the natural place to put this link is
    // beside `MailboxSendControl`, which renders once PER MAILBOX. The setting
    // it points at is per-CONNECTOR (`connector_permissions` is
    // UNIQUE (user_id, connector) — no instance column). Rendering it in the
    // loop would show it three times here and quietly teach a person the
    // setting is per-mailbox, which is the exact inference LUM-849 exists to
    // prevent. Three mailboxes, one link.
    const threeSources: typeof SOURCES = {
      sources: [
        { ...SOURCES.sources[0]!, id: "src-1", name: "Personal" },
        { ...SOURCES.sources[0]!, id: "src-2", name: "Work" },
        { ...SOURCES.sources[0]!, id: "src-3", name: "Old" },
      ],
      total: 3,
    };
    globalThis.fetch = mockFetch(undefined, threeSources) as unknown as typeof fetch;
    renderView(globalThis.fetch);

    await waitFor(() => {
      expect(screen.getByText("Work")).toBeInTheDocument();
    });
    const links = screen.getAllByRole("link", { name: /What Lumogis may do/i });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/settings/permissions");
  });
});

// --- LUM-769: the two always-visible state lines ------------------------- //
//
// `setEmailSmtp` had exactly one caller, inside the create form, so "let Lumogis
// send as me" could be chosen once and never revisited: the only route back was
// to remove and re-add the mailbox. These cover the card's half of the fix.

describe("LUM-769 send capability surface", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  /** Like `mockFetch` above, but this one knows about `/connector-credentials`. */
  function fetchWith(opts: {
    credentials?: { connector: string; instance: string }[] | "fail";
    sources?: typeof SOURCES;
    smtpSpy?: Mock<(url: string, init?: RequestInit) => void>;
    smtpResponse?: Record<string, unknown>;
  }) {
    return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-taxonomy")) return jsonResponse(200, TAXONOMY);
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      if (u.includes("/connector-credentials")) {
        if (opts.credentials === "fail") return jsonResponse(500, {});
        return jsonResponse(200, { items: opts.credentials ?? [] });
      }
      if (u.includes("/smtp") && init?.method === "PUT") {
        opts.smtpSpy?.(u, init);
        return jsonResponse(
          200,
          opts.smtpResponse ?? { ok: true, enabled: true, tested: true, error: null },
        );
      }
      if (u.includes("/sources") && !u.includes("/sync")) {
        return jsonResponse(200, opts.sources ?? SOURCES);
      }
      if (u.includes("/signals")) return jsonResponse(200, SIGNALS);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
  }

  it("shows both state lines without opening any disclosure", async () => {
    renderView(fetchWith({ credentials: [] }));

    // `toBeVisible`, NOT `getByText`: jsdom matches text inside a CLOSED
    // <details>, so `getByText` would pass even if these lines were hidden in a
    // disclosure — which is the exact thing this test exists to forbid.
    expect(await screen.findByText("Sending: off")).toBeVisible();
    expect(screen.getByText(/^Attachments:/)).toBeVisible();
  });

  it("says unknown, never off, when the credentials query fails", async () => {
    // MUTATION A's target. Rendering "off" would be a claim the client cannot
    // support, and it would invite a user to turn on something already on.
    renderView(fetchWith({ credentials: "fail" }));

    expect(await screen.findByText("Sending: unknown")).toBeVisible();
    expect(screen.queryByText("Sending: off")).toBeNull();
    expect(screen.getByRole("button", { name: /sending for Personal/i })).toBeDisabled();
  });

  it("says unknown, never off, while the credentials query is still loading", async () => {
    // The error path above is pinned; this is the same invariant for the brief
    // window before the first response. A fast "Turn on sending" during that
    // window would UPSERT with reuse_imap=true and drop a standalone password.
    let releaseCreds!: () => void;
    const credsGate = new Promise<void>((resolve) => {
      releaseCreds = resolve;
    });
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-taxonomy")) return jsonResponse(200, TAXONOMY);
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      if (u.includes("/connector-credentials")) {
        await credsGate;
        return jsonResponse(200, { items: [{ connector: "smtp", instance: "src-1" }] });
      }
      if (u.includes("/sources") && !u.includes("/sync")) return jsonResponse(200, SOURCES);
      if (u.includes("/signals")) return jsonResponse(200, SIGNALS);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;

    renderView(fetchImpl);

    expect(await screen.findByText("Sending: unknown")).toBeVisible();
    expect(screen.queryByText("Sending: off")).toBeNull();
    expect(screen.getByRole("button", { name: /Turn on sending for Personal/i })).toBeDisabled();

    releaseCreds();
    expect(await screen.findByText("Sending: on")).toBeVisible();
  });

  it("reads on when an smtp credential exists for THIS mailbox", async () => {
    renderView(fetchWith({ credentials: [{ connector: "smtp", instance: "src-1" }] }));
    expect(await screen.findByText("Sending: on")).toBeVisible();
  });

  it("does not read on from another mailbox's smtp row", async () => {
    renderView(fetchWith({ credentials: [{ connector: "smtp", instance: "src-2" }] }));
    expect(await screen.findByText("Sending: off")).toBeVisible();
  });

  it("issues one credentials request for the whole list", async () => {
    const fetchImpl = fetchWith({ credentials: [] });
    renderView(fetchImpl);
    await screen.findByText("Sending: off");

    const calls = (fetchImpl as unknown as Mock).mock.calls.filter((c) =>
      String(c[0]).includes("/connector-credentials"),
    );
    // Pins the no-N+1 property structurally: state arrives as a prop from the
    // parent's ONE query, never a query per component.
    expect(calls.length).toBe(1);
  });

  it("keeps a soft-save visible after the refetch it triggers", async () => {
    // THE SEAM TEST, and the only one here that crosses a mutation AND its
    // refetch. Every other test asserts a single render and cannot see this.
    //
    // The control's own `onChange` invalidates the credentials query. If
    // `unverified` were derived from that query, the refetch would recompute it
    // from the credential row — which is IDENTICAL whether or not the connect
    // test passed — and resolve to a plain "on" within one round-trip. The one
    // outcome most worth saying would flash and vanish.
    let credentials: { connector: string; instance: string }[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-taxonomy")) return jsonResponse(200, TAXONOMY);
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      if (u.includes("/connector-credentials")) return jsonResponse(200, { items: credentials });
      if (u.includes("/smtp") && init?.method === "PUT") {
        // The row now exists — so a refetch would say plain "on".
        credentials = [{ connector: "smtp", instance: "src-1" }];
        return jsonResponse(200, { ok: true, enabled: true, tested: false, error: null });
      }
      if (u.includes("/sources") && !u.includes("/sync")) return jsonResponse(200, SOURCES);
      if (u.includes("/signals")) return jsonResponse(200, SIGNALS);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;

    renderView(fetchImpl);
    await screen.findByText("Sending: off");
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending for Personal/i }));

    expect(await screen.findByText("Sending: on — not yet verified")).toBeVisible();
    // …and it must STILL say so after the invalidation has resolved.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText("Sending: on — not yet verified")).toBeVisible();
    expect(screen.queryByText("Sending: on")).toBeNull();
  });

  it("does not promise that attachments are filed automatically", async () => {
    // LUM-727 changed what the attachment control MEANS: nothing is filed
    // automatically, attachments are listed for review. A summary line saying
    // "Filing attachments: on" would reinstate the promise the control directly
    // below it retracts — the card contradicting itself in the reader's eye-line.
    renderView(
      fetchWith({
        credentials: [],
        // `SOURCES` is a literal, so its inferred element type has no
        // `attachment_ingest_enabled`. The field is real on the wire and optional
        // on `SourceRow`; widening through `unknown` keeps the fixture honest
        // without loosening the shared literal for every other test in this file.
        sources: {
          ...SOURCES,
          sources: [{ ...SOURCES.sources[0], attachment_ingest_enabled: true }],
        } as unknown as typeof SOURCES,
      }),
    );

    expect(await screen.findByText("Attachments: listed for review")).toBeVisible();
    // LUM-776 widened this from `/Filing attachments/` to cover the disclosure
    // summary too, which said "File attachments from this mailbox" — the copy
    // MOST users read, since a summary is what you see before deciding to open
    // it, while the correction ("Nothing is filed automatically") is inside.
    // Deliberately does NOT match "filed", which is the correct verb for what
    // happens when a user files an attachment themselves.
    expect(document.body.textContent).not.toMatch(/fil(e|ing) attachments/i);
    expect(screen.getByText("Attachments from this mailbox")).toBeVisible();
  });

  it("passes the auth method through to the send control", async () => {
    // LUM-777: an OAuth mailbox never uses a stored sending password, so the
    // disable confirm must not mention one.
    renderView(
      fetchWith({
        credentials: [{ connector: "smtp", instance: "src-1" }],
        sources: {
          ...SOURCES,
          sources: [{ ...SOURCES.sources[0], auth_method: "oauth" }],
        } as unknown as typeof SOURCES,
      }),
    );
    await screen.findByText("Sending: on");

    await userEvent.click(screen.getByRole("button", { name: /Turn off sending for Personal/i }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Receiving is unaffected");
    expect(dialog).not.toHaveTextContent("own sending password");
  });

  it("refreshes both queries when sending is turned on", async () => {
    const fetchImpl = fetchWith({ credentials: [] });
    renderView(fetchImpl);
    await screen.findByText("Sending: off");

    const before = (fetchImpl as unknown as Mock).mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending for Personal/i }));

    await waitFor(() => {
      const after = (fetchImpl as unknown as Mock).mock.calls.slice(before).map((c) => String(c[0]));
      expect(after.some((u) => u.includes("/connector-credentials"))).toBe(true);
      expect(after.some((u) => u.includes("/sources"))).toBe(true);
    });
  });
});
