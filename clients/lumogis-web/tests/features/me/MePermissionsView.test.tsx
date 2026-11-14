// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-849 — the autonomy page.
//
// The previous version of this file faked a connector called `c1` and answered
// the PUT with `204 No Content`. Neither is a thing the server does: `c1` is not
// in the action registry so the real route 404s it, and the real PUT returns
// `200` with a `ConnectorPermissionPublic` body. It proved the table rendered
// whatever the server said, and nothing about what the server actually says.
//
// §5a — every fake here produces what the real route produces: real connector
// ids, a `200` + body PUT, FastAPI's `{"detail": …}` error shape, and the exact
// `Warning` header value the route emits.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MePermissionsView } from "../../../src/features/me/MePermissionsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

const USER = { id: "u1", email: "u@home.lan", role: "user" as const };

/** The route's advisory, verbatim from `_ACTION_REGISTRY_UNAVAILABLE_HEADER`. */
const REGISTRY_WARNING = '199 - "action registry unavailable; connector unvalidated"';

function row(connector: string, mode: "ASK" | "DO", is_default = false) {
  return { connector, mode, scopes: [], is_default, updated_at: "2026-01-01T00:00:00Z" };
}

/** The healthy shape: one row per known connector, lazily defaulting to ASK. */
const HEALTHY = [row("smtp", "ASK", true), row("imap", "ASK", true)];

interface Opts {
  rows?: unknown;
  listStatus?: number;
  listHeaders?: Record<string, string>;
  putStatus?: number;
  onPut?: (body: unknown) => void;
  /** Held PUT: the request stays in-flight until this resolves. In-flight
   *  assertions must latch, not race a wall-clock `setTimeout` — that passed
   *  in isolation and failed under full vitest load. */
  putHold?: Promise<void>;
}

function deferredPut(): { hold: Promise<void>; release: () => void } {
  let release!: () => void;
  const hold = new Promise<void>((r) => {
    release = r;
  });
  return { hold, release };
}

function mockFetch(opts: Opts = {}) {
  const {
    rows = HEALTHY,
    listStatus = 200,
    listHeaders,
    putStatus = 200,
    onPut,
    putHold,
  } = opts;
  return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/api/v1/auth/me")) return jsonResponse(200, USER);
    if (u.includes("/me/permissions/") && init?.method === "PUT") {
      onPut?.(JSON.parse(String(init.body)));
      if (putHold) await putHold;
      if (putStatus !== 200) {
        // FastAPI's real error shape — `apiErrorFrom` reads `detail` from it.
        return jsonResponse(putStatus, { detail: "unknown_connector" });
      }
      const connector = decodeURIComponent(u.split("/me/permissions/")[1]!);
      const mode = JSON.parse(String(init.body)).mode as "ASK" | "DO";
      return jsonResponse(200, row(connector, mode));
    }
    if (u.includes("/api/v1/me/permissions")) {
      if (listStatus !== 200) return jsonResponse(listStatus, { detail: "boom" });
      return jsonResponse(200, rows, listHeaders);
    }
    return jsonResponse(404, { detail: "not found" });
  });
}

function renderView(fetchImpl: ReturnType<typeof mockFetch>) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <MePermissionsView />
    </AuthProvider>,
  );
}

describe("MePermissionsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders two questions in plain words, not connector ids", async () => {
    renderView(mockFetch());
    await waitFor(() => {
      expect(screen.getByText("Send email as you")).toBeInTheDocument();
    });
    expect(screen.getByText("Tidy your mailbox")).toBeInTheDocument();

    // Scoped to VISIBLE TEXT, deliberately. The radio inputs legitimately carry
    // value="ASK"/"DO" as form values; asserting over attributes would make this
    // unsatisfiable, and the natural "fix" would be to weaken it.
    const text = document.body.textContent ?? "";
    for (const raw of ["imap", "smtp", "ASK", "DO"]) {
      expect(text).not.toContain(raw);
    }
  });

  it("renders exactly one control group per connector", async () => {
    // The grain assertion. The plan asked for this "with three mailboxes
    // present" — but this page never fetches mailboxes at all, so such a
    // fixture would assert nothing. The honest form of the claim is that the
    // control count tracks CONNECTORS: two connectors, two groups, regardless
    // of how many mailboxes exist. The per-mailbox trap lives on Email signals
    // (the cross-link), and is tested there with three mailboxes.
    renderView(mockFetch());
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());
    expect(document.querySelectorAll("fieldset[data-testid^='perm-group-']")).toHaveLength(2);
    expect(screen.getAllByRole("radio")).toHaveLength(4);
  });

  it("states the scope on every question", async () => {
    renderView(mockFetch());
    await waitFor(() => {
      expect(screen.getAllByText("Applies to all your mailboxes.")).toHaveLength(2);
    });
  });

  it("marks a row at the lazy default, and an explicit row not", async () => {
    renderView(mockFetch({ rows: [row("smtp", "ASK", true), row("imap", "DO", false)] }));
    await waitFor(() => {
      expect(screen.getByTestId("perm-group-smtp")).toBeInTheDocument();
    });
    expect(screen.getByTestId("perm-group-smtp").textContent).toMatch(/\(default\)/);
    expect(screen.getByTestId("perm-group-imap").textContent).not.toMatch(/\(default\)/);
  });

  it("sends a PUT with the chosen mode", async () => {
    const seen: unknown[] = [];
    globalThis.fetch = mockFetch({ onPut: (b) => seen.push(b) }) as unknown as typeof fetch;
    renderView(globalThis.fetch as unknown as ReturnType<typeof mockFetch>);
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("radio", { name: /Just send/i }));
    await waitFor(() => expect(seen).toEqual([{ mode: "DO" }]));
  });

  // --- B1: a known question whose row the server did not send -------------- //

  it("still renders a question when its row is missing", async () => {
    // The degrade shape: the route returns explicit rows ONLY, so a user who
    // never changed their imap setting has no imap row. Iterating the response
    // would drop the whole question off a page whose job is listing what may
    // act on your behalf.
    renderView(mockFetch({ rows: [row("smtp", "ASK", true)] }));
    await waitFor(() => {
      expect(screen.getByText("Tidy your mailbox")).toBeInTheDocument();
    });
    const group = screen.getByTestId("perm-group-imap");
    expect(group.textContent).toMatch(/couldn't load this setting/i);
    expect(group.querySelectorAll("input[type=radio]")).toHaveLength(0);
  });

  it("never renders an absent row as the default", async () => {
    // `is_default: true` is a statement the server makes. An absent row is the
    // absence of any statement, and showing it as "Ask me first (default)"
    // would be inventing a reassurance.
    renderView(mockFetch({ rows: [row("smtp", "ASK", true)] }));
    await waitFor(() => expect(screen.getByTestId("perm-group-imap")).toBeInTheDocument());
    expect(screen.getByTestId("perm-group-imap").textContent).not.toMatch(/\(default\)/);
  });

  // --- B2: a row the client has no words for ------------------------------- //

  it("renders an unmapped connector honestly rather than dropping it", async () => {
    renderView(mockFetch({ rows: [...HEALTHY, row("capability.summarise", "ASK", true)] }));
    await waitFor(() => {
      expect(screen.getByTestId("perm-group-capability.summarise")).toBeInTheDocument();
    });
    const group = screen.getByTestId("perm-group-capability.summarise");
    expect(group.textContent).toContain("capability.summarise");
    expect(group.textContent).toMatch(/act on your behalf/i);
  });

  it("renders both a mapped and an unmapped row", async () => {
    // The union assertion. Fails under EITHER single-input iteration strategy:
    // response-only loses imap, known-map-only loses the capability row.
    renderView(mockFetch({ rows: [row("smtp", "ASK", true), row("capability.x", "DO")] }));
    await waitFor(() => expect(screen.getByTestId("perm-group-smtp")).toBeInTheDocument());
    expect(screen.getByTestId("perm-group-imap")).toBeInTheDocument();
    expect(screen.getByTestId("perm-group-capability.x")).toBeInTheDocument();
  });

  // --- B3: the warn-code ---------------------------------------------------- //

  it("says the list may be incomplete when the route sends 199", async () => {
    renderView(
      mockFetch({ rows: [row("smtp", "ASK", true)], listHeaders: { Warning: REGISTRY_WARNING } }),
    );
    await waitFor(() => {
      expect(screen.getByText(/may be incomplete/i)).toBeInTheDocument();
    });
  });

  it("does not say incomplete for a non-199 Warning", async () => {
    // `Warning` is a general-purpose header. A cache emitting `110 Response is
    // stale` must not raise a false alarm on a complete list.
    renderView(
      mockFetch({ listHeaders: { Warning: '110 - "Response is stale"' } }),
    );
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());
    expect(screen.queryByText(/may be incomplete/i)).not.toBeInTheDocument();
  });

  it("does not say incomplete on a healthy load", async () => {
    renderView(mockFetch());
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());
    expect(screen.queryByText(/may be incomplete/i)).not.toBeInTheDocument();
  });

  it("keeps the groups that loaded interactive while incomplete", async () => {
    // The setting a person most wants during an outage is the one that stops
    // Lumogis sending as them. A banner must not disable the form.
    renderView(
      mockFetch({ rows: [row("smtp", "ASK", true)], listHeaders: { Warning: REGISTRY_WARNING } }),
    );
    await waitFor(() => expect(screen.getByText(/may be incomplete/i)).toBeInTheDocument());
    const radios = screen.getByTestId("perm-group-smtp").querySelectorAll("input[type=radio]");
    expect(radios).toHaveLength(2);
    radios.forEach((r) => expect(r).not.toBeDisabled());
  });

  // --- B4: the request failed ----------------------------------------------- //

  it("surfaces a 500 as an error, never as an empty page", async () => {
    // Reachable only because api/permissions.ts throws on non-2xx. If it
    // resolved the failed request instead, this page would render an empty
    // permission list — for this surface, the worst available lie.
    renderView(mockFetch({ listStatus: 500 }));
    await waitFor(() => {
      expect(screen.getByText(/couldn't load what Lumogis may do/i)).toBeInTheDocument();
    });
    expect(screen.queryByText("Send email as you")).not.toBeInTheDocument();
  });

  it("says so when a save is rejected, and leaves the setting alone", async () => {
    globalThis.fetch = mockFetch({ putStatus: 404 }) as unknown as typeof fetch;
    renderView(globalThis.fetch as unknown as ReturnType<typeof mockFetch>);
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("radio", { name: /Just send/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/didn't save/i);
    });
    // No optimistic update, so there is nothing to revert — the control still
    // shows what the server last confirmed. Scoped to the smtp group: BOTH
    // groups offer an "Ask me first" radio, which is the point of the design
    // and would make an unscoped query ambiguous.
    expect(within(screen.getByTestId("perm-group-smtp")).getByRole("radio", {
      name: /Ask me first/i,
    })).toBeChecked();
  });

  // --- B5: a mutation is in flight ------------------------------------------ //

  it("disables the group while its save is in flight", async () => {
    const { hold, release } = deferredPut();
    globalThis.fetch = mockFetch({ putHold: hold }) as unknown as typeof fetch;
    renderView(globalThis.fetch as unknown as ReturnType<typeof mockFetch>);
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());

    try {
      await userEvent.click(screen.getByRole("radio", { name: /Just send/i }));
      await waitFor(() => {
        expect(screen.getByRole("radio", { name: /Just send/i })).toBeDisabled();
      });
    } finally {
      release();
    }
  });

  it("keeps a group disabled when the OTHER group is clicked mid-flight", async () => {
    // The cross-connector race, found by code review. A single shared
    // useMutation tracks only the most recently dispatched call —
    // MutationObserver.mutate() detaches from the in-flight mutation before
    // attaching the new one — so scoping by `variables.connector` LOOKS like it
    // isolates groups and does not. With one shared mutation this test fails:
    // clicking imap re-enables smtp's radios while smtp's PUT is still in
    // flight, and a second click there writes the same change twice.
    //
    const { hold, release } = deferredPut();
    globalThis.fetch = mockFetch({ putHold: hold }) as unknown as typeof fetch;
    renderView(globalThis.fetch as unknown as ReturnType<typeof mockFetch>);
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());

    try {
      await userEvent.click(screen.getByRole("radio", { name: /Just send/i }));
      const smtpJustSend = within(screen.getByTestId("perm-group-smtp")).getByRole("radio", {
        name: /Just send/i,
      });
      await waitFor(() => expect(smtpJustSend).toBeDisabled());

      await userEvent.click(screen.getByRole("radio", { name: /Just do it/i }));

      // smtp's PUT has not resolved, so smtp must still be locked.
      expect(smtpJustSend).toBeDisabled();
    } finally {
      release();
    }
  });

  it("shows a failure on the group that failed, even after another group is used", async () => {
    // The silent half of the same defect: with a shared mutation,
    // `variables.connector` is stuck on the second group, so the first group's
    // failure can never render and the user is told nothing.
    const { hold, release } = deferredPut();
    globalThis.fetch = mockFetch({ putStatus: 404, putHold: hold }) as unknown as typeof fetch;
    renderView(globalThis.fetch as unknown as ReturnType<typeof mockFetch>);
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());

    try {
      await userEvent.click(screen.getByRole("radio", { name: /Just send/i }));
      await userEvent.click(screen.getByRole("radio", { name: /Just do it/i }));
    } finally {
      release();
    }

    await waitFor(() => {
      expect(
        within(screen.getByTestId("perm-group-smtp")).getByRole("alert").textContent,
      ).toMatch(/didn't save/i);
    });
  });

  it("surfaces a 401 with session copy and no doctor hint", async () => {
    // The 401 arm was reachable but only asserted by inspection. `client.fetch`
    // retries once after a refresh attempt; a genuine 401 survives that when the
    // refresh cookie is gone too.
    renderView(mockFetch({ listStatus: 401 }));
    await waitFor(() => {
      expect(screen.getByText(/session expired or you are not signed in/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/lumogis doctor/i)).not.toBeInTheDocument();
  });

  it("fires exactly one PUT when the second click lands before the re-render", async () => {
    // The disabled guard only takes effect on the next render. Two clicks in the
    // same tick (a fast double-click) both reach onChange before React re-renders,
    // so only a synchronous in-flight guard keeps this to one write. Found by the
    // e2e double-click test failing 5 runs in 10 (2026-11-14 full-dev RC).
    const puts: unknown[] = [];
    const { hold, release } = deferredPut();
    globalThis.fetch = mockFetch({
      putHold: hold,
      onPut: (b) => puts.push(b),
    }) as unknown as typeof fetch;
    renderView(globalThis.fetch as unknown as ReturnType<typeof mockFetch>);
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());

    try {
      const justSend = screen.getByRole("radio", { name: /Just send/i });
      act(() => {
        fireEvent.click(justSend);
        fireEvent.click(justSend);
      });
      await waitFor(() => expect(puts).toHaveLength(1));
      await new Promise((r) => setTimeout(r, 20));
      expect(puts).toHaveLength(1);
    } finally {
      release();
    }
  });

  it("fires exactly one PUT when a control is clicked twice quickly", async () => {
    // With no optimistic update the radio cannot move until the PUT and its
    // refetch both land. Without the disabled guard a person sees nothing
    // happen, clicks again, and writes a permission change twice — a false
    // statement in the audit trail about a security setting.
    const puts: unknown[] = [];
    const { hold, release } = deferredPut();
    globalThis.fetch = mockFetch({
      putHold: hold,
      onPut: (b) => puts.push(b),
    }) as unknown as typeof fetch;
    renderView(globalThis.fetch as unknown as ReturnType<typeof mockFetch>);
    await waitFor(() => expect(screen.getByText("Send email as you")).toBeInTheDocument());

    try {
      const justSend = screen.getByRole("radio", { name: /Just send/i });
      await userEvent.click(justSend);
      await waitFor(() => expect(justSend).toBeDisabled());
      await userEvent.click(justSend);
      expect(puts).toHaveLength(1);
    } finally {
      release();
    }
  });
});
