// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-729 — the warning that stops Lumogis quietly emptying paperless's inbox.
//
// Folder-sort moves classified mail out of INBOX. A paperless mail rule watches
// INBOX. Both poll on their own clock, so roughly one message in three is taken
// before paperless ever sees it — and nothing anywhere reports it.
//
// Three things are load-bearing here and each was a review finding, not a first
// draft:
//
//   * **The first save must go WITHOUT the acknowledgement.** A component that
//     always sent `true` would turn the gate into a dismissible banner encoded in
//     JSON, which is precisely the shape the design rejects. This is the one
//     property the server genuinely cannot enforce, so it is tested here.
//   * **Labels-only is offered to EVERY provider.** The plan originally withheld
//     it from non-Gmail users because their provider does not *display* labels.
//     That is a fact about what a human sees, not about whether the message stays
//     in the inbox — which is the entire bug. Withholding it would have denied the
//     only safe option to the users with the fewest options.
//   * **Undo must be reachable and honestly described.** The scope was hardcoded
//     to "all", so the folder-only put-back was unreachable from the UI. A
//     warning implying recoverability beside a recovery path nobody can reach is
//     a promise the product does not keep.

import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiClient, ApiError } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { EmailCleanupPass } from "../../../src/features/signals/EmailCleanupPass";
import { EmailOrganizeSettings } from "../../../src/features/signals/EmailOrganizeSettings";
import {
  STARVATION_CODE,
  starvationWarning,
} from "../../../src/features/signals/externalConsumerWarning";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

const TAX = {
  categories: [
    { slug: "bills_finance", label: "Bills & finance", enabled: true, is_custom: false, is_protected: false, sort: 0 },
    { slug: "other", label: "Other", enabled: true, is_custom: false, is_protected: true, sort: 0 },
  ],
};

const REFUSAL = {
  code: STARVATION_CODE,
  message:
    "You also use paperless. Sorting mail into folders moves it out of your inbox, and a paperless mail rule that watches the inbox will miss whatever Lumogis takes first.",
  alternative: "Labels alone do not move mail, so paperless still receives everything.",
  recovery:
    "Undo puts messages back in your inbox. It does not give paperless the documents it missed while they were away.",
};

/** Bodies sent to PATCH /organize, in order. The ORDER is the point: the gate
 *  means nothing if the first one already carries the acknowledgement. */
let patches: Array<Record<string, unknown>>;
let undos: Array<Record<string, unknown>>;

function renderView(fetchImpl: typeof fetch, labelVisible = true) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <EmailOrganizeSettings sourceId="src-1" labelVisible={labelVisible} />
    </AuthProvider>,
  );
}

/** Refuses any organize PATCH that does not carry the acknowledgement — the
 *  server's actual behaviour, so the component is driven by the real handshake
 *  rather than by a single canned failure. */
function gatingFetch() {
  return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/organize/undo")) {
      const body = JSON.parse(String(init?.body ?? "{}"));
      undos.push(body);
      return jsonResponse(202, { undo_pending: true, scope: body.scope });
    }
    if (u.includes("/organize") && init?.method === "PATCH") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      patches.push(body);
      if (body.organize_folder && !body.acknowledge_external_consumer) {
        return jsonResponse(422, { detail: REFUSAL });
      }
      return jsonResponse(202, { updated: true, apply_to_existing: !!body.apply_to_existing });
    }
    if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
    if (u.includes("/email-taxonomy")) return jsonResponse(200, TAX);
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
}

async function armFolderSortAndSave() {
  fireEvent.click(await screen.findByRole("checkbox", { name: /Sort my emails into folders/i }));
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
}

describe("recognising the refusal", () => {
  it("matches on the CODE, never on the copy", () => {
    // The prose is allowed to change without a contract break. A UI that
    // branched on wording would silently stop recognising the refusal the first
    // time someone reworded it.
    const err = new ApiError(422, JSON.stringify({ detail: { ...REFUSAL, message: "reworded" } }));
    expect(starvationWarning(err)?.message).toBe("reworded");
  });

  it("is null for any other error", () => {
    expect(starvationWarning(new ApiError(422, JSON.stringify({ detail: { code: "other" } })))).toBeNull();
    expect(starvationWarning(new ApiError(500, "boom"))).toBeNull();
    expect(starvationWarning(new ApiError(422, "not json at all"))).toBeNull();
    expect(starvationWarning(new Error("nope"))).toBeNull();
  });
});

describe("the starvation warning", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    patches = [];
    undos = [];
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("does NOT acknowledge on the first save attempt", async () => {
    // The property the server cannot enforce. If this regresses, the gate still
    // exists on paper and protects nobody.
    renderView(gatingFetch());
    await armFolderSortAndSave();

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0].acknowledge_external_consumer).toBeUndefined();
  });

  it("shows the consequence rather than a generic failure", async () => {
    renderView(gatingFetch());
    await armFolderSortAndSave();

    const dialog = await screen.findByTestId("starvation-warning");
    expect(dialog.textContent).toMatch(/paperless/i);
    expect(dialog.textContent).toMatch(/inbox/i);
    expect(screen.queryByText(/Could not save/i)).toBeNull();
  });

  it("does not save anything when it is refused", async () => {
    renderView(gatingFetch());
    await armFolderSortAndSave();

    await screen.findByTestId("starvation-warning");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("saves WITH the acknowledgement only after the user continues", async () => {
    renderView(gatingFetch());
    await armFolderSortAndSave();
    fireEvent.click(await screen.findByTestId("starvation-continue"));

    await waitFor(() => expect(patches).toHaveLength(2));
    expect(patches[1].acknowledge_external_consumer).toBe(true);
    expect(await screen.findByRole("status")).toBeTruthy();
  });

  it("offers labels-only on a provider that does NOT display labels", async () => {
    // The reversal. What matters for paperless is only that the message stays in
    // the inbox, and labels-only guarantees that everywhere.
    renderView(gatingFetch(), false);
    await armFolderSortAndSave();

    await screen.findByTestId("starvation-warning");
    expect(screen.getByRole("button", { name: /Use labels instead/i })).toBeTruthy();
  });

  it("adds the visibility caveat only where labels are invisible", async () => {
    renderView(gatingFetch(), false);
    await armFolderSortAndSave();
    await screen.findByTestId("starvation-warning");
    expect(screen.getByTestId("starvation-label-caveat")).toBeTruthy();
  });

  it("omits the caveat where labels ARE visible", async () => {
    renderView(gatingFetch(), true);
    await armFolderSortAndSave();
    await screen.findByTestId("starvation-warning");
    expect(screen.queryByTestId("starvation-label-caveat")).toBeNull();
  });

  it("does not promise that undo gives paperless what it missed", async () => {
    // Undo puts messages back in the inbox. It does not hand paperless the
    // documents it never saw. Implying otherwise would be the same silent hole
    // with a reassuring message on top.
    renderView(gatingFetch());
    await armFolderSortAndSave();

    const recovery = await screen.findByTestId("starvation-recovery");
    expect(recovery.textContent).toMatch(/does not/i);
  });

  it("switching to labels turns folder-sort off without saving behind the user's back", async () => {
    renderView(gatingFetch());
    await armFolderSortAndSave();
    fireEvent.click(await screen.findByRole("button", { name: /Use labels instead/i }));

    expect(screen.getByRole("checkbox", { name: /Sort my emails into folders/i })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Label my emails/i })).toBeChecked();
    // Still one PATCH: the user sees what they are about to agree to first.
    expect(patches).toHaveLength(1);
  });

  it("editing anything clears a warning shown for a different request", async () => {
    // An acknowledgement is of a specific consequence. Carrying the warning
    // across an edit would let the user agree to something they never saw.
    renderView(gatingFetch());
    await armFolderSortAndSave();
    await screen.findByTestId("starvation-warning");

    fireEvent.click(screen.getByRole("checkbox", { name: /Also apply to existing mail/i }));

    expect(screen.queryByTestId("starvation-warning")).toBeNull();
  });
});

// The scope picker is a Base UI Select (a combobox that opens a listbox), not a
// native <select>, so the choice is a click, not a change event.
async function chooseUndoScope(option: RegExp) {
  await userEvent.click(await screen.findByRole("combobox", { name: /What to undo/i }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

describe("undo is reachable at the scope the user means", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    patches = [];
    undos = [];
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("can request the folder-only put-back", async () => {
    // Previously hardcoded to "all", so a user who discovered the starvation
    // after the fact could only undo by also throwing away every label.
    renderView(gatingFetch());
    await chooseUndoScope(/^Folder sorting only/);
    fireEvent.click(screen.getByRole("button", { name: /^Undo$/ }));

    await waitFor(() => expect(undos).toHaveLength(1));
    expect(undos[0].scope).toBe("folder");
  });

  it("still defaults to undoing everything", async () => {
    renderView(gatingFetch());
    fireEvent.click(await screen.findByRole("button", { name: /^Undo$/ }));

    await waitFor(() => expect(undos).toHaveLength(1));
    expect(undos[0].scope).toBe("all");
  });

  it("says what it is putting back, not just that it started", async () => {
    renderView(gatingFetch());
    await chooseUndoScope(/^Folder sorting only/);
    fireEvent.click(screen.getByRole("button", { name: /^Undo$/ }));

    expect((await screen.findByRole("status")).textContent).toMatch(/inbox/i);
  });
});

// --- door 2: the cleanup approval has the same refusal and needed the same UI --
//
// Found by re-reading the call sites after the backend was already pushed, which
// is the LUM-724 pattern one more time: the guard reached both services and both
// routes, and stopped at one of the two web surfaces. Without this the user would
// meet "Something went wrong — try again" on a refusal that trying again can
// never clear.

describe("the cleanup approval", () => {
  let originalFetch: typeof fetch;
  let approvals: Array<Record<string, unknown>>;

  const READY_JOB = {
    state: "ready",
    kind: "window",
    progress: 1,
    counts: { scanned: 40, classified: 40, labeled: 40, executed: 0 },
    proposals: [
      { category: "bills_finance", label: "Bills & finance", count: 3, label_visible: true, can_move: true },
    ],
  };

  function cleanupFetch() {
    return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      const method = init?.method ?? "GET";
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/cleanup-spam")) return jsonResponse(200, { spam_count: 0, trash_available: false });
      if (u.includes("/approve") && method === "POST") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        approvals.push(body);
        if (!body.acknowledge_external_consumer) return jsonResponse(422, { detail: REFUSAL });
        return jsonResponse(202, { approved: 1 });
      }
      if (/\/cleanup\/[^/]+$/.test(u) && method === "GET") return jsonResponse(200, READY_JOB);
      if (u.endsWith("/cleanup") && method === "POST") return jsonResponse(202, { job_id: "job-1", state: "backfilling" });
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
  }

  async function approveBills(fetchImpl: typeof fetch) {
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <EmailCleanupPass sourceId="src-1" labelVisible />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText("Start cleanup"));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Bills/ }));
    fireEvent.click(screen.getByText("File selected into folders"));
  }

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    approvals = [];
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows the consequence instead of a generic failure", async () => {
    await approveBills(cleanupFetch());

    const dialog = await screen.findByTestId("cleanup-starvation-warning");
    expect(dialog.textContent).toMatch(/paperless/i);
    expect(screen.queryByText(/Something went wrong/i)).toBeNull();
  });

  it("does not acknowledge on the first attempt", async () => {
    await approveBills(cleanupFetch());

    await waitFor(() => expect(approvals).toHaveLength(1));
    expect(approvals[0].acknowledge_external_consumer).toBeUndefined();
  });

  it("files them once the user continues", async () => {
    await approveBills(cleanupFetch());
    fireEvent.click(await screen.findByTestId("cleanup-starvation-continue"));

    await waitFor(() => expect(approvals).toHaveLength(2));
    expect(approvals[1].acknowledge_external_consumer).toBe(true);
    expect(approvals[1].actions).toEqual([{ category: "bills_finance", move: true }]);
  });

  it("changing the selection clears the warning", async () => {
    // The acknowledgement is of a specific set of categories.
    await approveBills(cleanupFetch());
    await screen.findByTestId("cleanup-starvation-warning");

    fireEvent.click(screen.getByRole("checkbox", { name: /Bills/ }));

    expect(screen.queryByTestId("cleanup-starvation-warning")).toBeNull();
  });
});

// --- LUM-729 follow-up (FP-076): the undo sweep can now give up -------------
//
// Before this, one message that could not be reversed left `organize_undo_scope`
// set forever — and because the poll tick skips the reconcile sweep while an undo
// reports work, that permanently disabled organizing for the mailbox, silently,
// while re-opening an IMAP session every tick to retry the same doomed row.
//
// The bound alone would not have been enough. A give-up nobody can see trades one
// invisible failure for another, which is the complaint this whole feature exists
// about — so it is surfaced where the un-reversed messages are listed.

describe("the undo gave up", () => {
  let originalFetch: typeof fetch;

  function logFetch(gaveUp: string | null, unreversed = 0) {
    return vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/organize/log")) {
        return jsonResponse(200, {
          entries: [],
          undo_gave_up_at: gaveUp,
          undo_unreversed: unreversed,
        });
      }
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-taxonomy")) return jsonResponse(200, TAX);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
  }

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    patches = [];
    undos = [];
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("says so, and says how many, and says organizing resumed", async () => {
    renderView(logFetch("2026-07-27T10:00:00Z", 3));
    fireEvent.click(await screen.findByRole("button", { name: /Show activity/i }));

    const alert = await screen.findByTestId("undo-gave-up");
    expect(alert.textContent).toMatch(/3 messages/);
    expect(alert.textContent).toMatch(/resumed/i);
  });

  it("gets the singular right", async () => {
    // One message is the common case — it takes exactly one doomed row to wedge
    // the sweep — so "1 messages" would be the string most users actually see.
    renderView(logFetch("2026-07-27T10:00:00Z", 1));
    fireEvent.click(await screen.findByRole("button", { name: /Show activity/i }));

    expect((await screen.findByTestId("undo-gave-up")).textContent).toMatch(/1 message[^s]/);
  });

  it("says nothing when the undo did not give up", async () => {
    renderView(logFetch(null));
    fireEvent.click(await screen.findByRole("button", { name: /Show activity/i }));

    await screen.findByTestId("organize-log");
    expect(screen.queryByTestId("undo-gave-up")).toBeNull();
  });
});
