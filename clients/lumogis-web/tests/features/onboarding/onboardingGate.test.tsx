// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { ModalFrame } from "../../../src/features/onboarding/modalFrame";
import { OnboardingGate } from "../../../src/features/onboarding/OnboardingGate";
import { LocationProbe } from "../../helpers/locationProbe";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ModalFrame", () => {
  it("Escape invokes onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ModalFrame open titleId="t1" onClose={onClose}>
        <h2 id="t1">Title</h2>
        <button type="button">Inside</button>
      </ModalFrame>,
    );
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Tab from last focusable wraps focus to first", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ModalFrame open titleId="t1" onClose={onClose}>
        <h2 id="t1">Title</h2>
        <button type="button">First</button>
        <button type="button">Last</button>
      </ModalFrame>,
    );
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });

    await waitFor(() => {
      expect(first).toHaveFocus();
    });

    await user.tab();
    expect(last).toHaveFocus();

    await user.tab();
    expect(first).toHaveFocus();
  });

  it("Shift+Tab from first focusable wraps focus to last", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ModalFrame open titleId="t1" onClose={onClose}>
        <h2 id="t1">Title</h2>
        <button type="button">First</button>
        <button type="button">Last</button>
      </ModalFrame>,
    );
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });

    await waitFor(() => {
      expect(first).toHaveFocus();
    });

    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(last).toHaveFocus();
  });
});

describe("OnboardingGate", () => {
  it("does not render modal while onboarding query is pending", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let resolveOnb!: (v: Response) => void;
    const onbPromise = new Promise<Response>((resolve) => {
      resolveOnb = resolve;
    });
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/auth/me")) {
        return jsonResponse(200, { id: "u1", email: "a@a", role: "user" });
      }
      if (url.endsWith("/api/v1/me/onboarding")) {
        return onbPromise;
      }
      throw new Error(`unexpected: ${url}`);
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <MemoryRouter>
          <OnboardingGate />
        </MemoryRouter>
      </AuthProvider>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    resolveOnb!(jsonResponse(200, { completed_at: null }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("shows retry banner when GET onboarding fails", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/auth/me")) {
        return jsonResponse(200, { id: "u1", email: "a@a", role: "user" });
      }
      if (url.endsWith("/api/v1/me/onboarding")) {
        return jsonResponse(503, { detail: "database_unavailable" });
      }
      throw new Error(`unexpected: ${url}`);
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <MemoryRouter>
          <OnboardingGate />
        </MemoryRouter>
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// LUM-282 — the onboarding handoff.
//
// These live HERE and not in OnboardingModal.test.tsx, and that is load-bearing.
// `OnboardingModal` hard-codes `<ModalFrame open …>` and has no self-dismiss, so
// a test that renders it directly keeps a dialog on screen forever: "assert the
// modal closed" there is not a test that goes red then green, it is a test that
// can never pass. `OnboardingGate` owns the query whose state makes it vanish.
//
// Two further harness rules, both learned the hard way:
//
//   * Each test creates a real `<div id="root">` and renders into it. Without
//     one, `ModalFrame`'s `document.getElementById("root")` finds nothing, sets
//     no attributes, and "assert #root is not inert" passes vacuously against an
//     element that never existed.
//   * Dialog queries go through `screen`, never through `render()`'s return
//     value. `container: root` binds those queries to `#root`, but the overlay
//     is portaled to `document.body` — measured: container-bound querying
//     reports ZERO dialogs while the modal is on screen, so the assertion would
//     pass before and after the click alike.
// ---------------------------------------------------------------------------

function handoffSetup(patch: { status?: number; hold?: Promise<void> } = {}) {
  const tokens = new AccessTokenStore();
  tokens.set("tok");
  const patchCalls: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/auth/me")) {
      return jsonResponse(200, { id: "u1", email: "a@a", role: "user" });
    }
    // GET and PATCH share this path — branching on URL alone (as the tests
    // above could afford to) would answer the PATCH with the GET's body.
    if (url.endsWith("/api/v1/me/onboarding")) {
      if (init?.method === "PATCH") {
        patchCalls.push(url);
        if (patch.hold) await patch.hold;
        const status = patch.status ?? 200;
        return status === 200
          ? jsonResponse(200, { completed_at: "2026-09-03T10:00:00Z" })
          : jsonResponse(status, { detail: "database_unavailable" });
      }
      return jsonResponse(200, { completed_at: null });
    }
    throw new Error(`unexpected: ${url}`);
  });
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);

  render(
    <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
      <MemoryRouter initialEntries={["/chat"]}>
        <LocationProbe stateKeys={["fromOnboarding"]} />
        <OnboardingGate />
      </MemoryRouter>
    </AuthProvider>,
    { container: root },
  );

  return { root, patchCalls };
}

async function advanceToHandoff(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("dialog");
  await user.click(screen.getByRole("button", { name: /^Next$/ }));
  await user.click(screen.getByRole("button", { name: /^Next$/ }));
  return screen.getByRole("button", { name: /Set up documents now/ });
}

describe("OnboardingGate — LUM-282 handoff", () => {
  it("completes onboarding, closes the modal, and lands on /me/sources", async () => {
    const user = userEvent.setup();
    const { root, patchCalls } = handoffSetup();
    const button = await advanceToHandoff(user);

    // Positive first, or the negative below is worthless: prove the modal is
    // really open and #root really is inert while it is.
    expect({
      dialogs: screen.queryAllByRole("dialog").length,
      rootInert: root.hasAttribute("inert"),
      rootAriaHidden: root.getAttribute("aria-hidden"),
    }).toEqual({ dialogs: 1, rootInert: true, rootAriaHidden: "true" });

    await user.click(button);

    // `waitFor`, not a synchronous assert: query-core's notifyManager defers
    // observer notification by a macrotask, so `navigate` runs BEFORE the gate
    // re-renders and the modal is briefly mounted on the new route.
    await waitFor(() => {
      expect({
        patches: patchCalls.length,
        route: screen.getByTestId("location-probe").textContent,
        dialogs: screen.queryAllByRole("dialog").length,
        rootInert: root.hasAttribute("inert"),
        rootAriaHidden: root.getAttribute("aria-hidden"),
      }).toEqual({
        patches: 1,
        route: "/me/sources",
        dialogs: 0,
        rootInert: false,
        rootAriaHidden: null,
      });
    });

    // The navigation carries the state SourcesView reads to take focus.
    expect(screen.getByTestId("location-probe").dataset.fromOnboarding).toBe("true");
  });

  it("does not navigate, and does not reject, when the completion fails", async () => {
    const user = userEvent.setup();
    const { root, patchCalls } = handoffSetup({ status: 503 });
    const button = await advanceToHandoff(user);

    const unhandled = vi.fn();
    // Works because vitest.config.ts sets no `pool`, so the default forks pool
    // runs this file in a process that observes its own rejections. If anyone
    // pins `pool: "threads"`, this assertion silently stops observing anything.
    process.on("unhandledRejection", unhandled);
    await user.click(button);
    await screen.findByRole("alert");
    // An unhandled rejection is reported asynchronously — unsubscribing before
    // yielding a macrotask would observe nothing and pass for the wrong reason.
    await new Promise((r) => setTimeout(r, 0));
    process.off("unhandledRejection", unhandled);

    expect({
      unhandledRejections: unhandled.mock.calls.length,
      patches: patchCalls.length,
      route: screen.getByTestId("location-probe").textContent,
      dialogs: screen.queryAllByRole("dialog").length,
      rootInert: root.hasAttribute("inert"),
    }).toEqual({
      unhandledRejections: 0,
      patches: 1,
      route: "/chat",
      dialogs: 1,
      rootInert: true,
    });

    // Non-empty, not merely present: OnboardingModal renders the alert only
    // when `completeError.length > 0`, so an empty string would render nothing
    // and the click would appear to do nothing at all.
    expect(screen.getByRole("alert").textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });

  // Found by the §7a code review: `disabled={busy}` gates the BUTTONS, but ESC
  // is a document-level keydown in ModalFrame that calls onClose -> onSkip ->
  // finish() with no button involved. Before the `busyRef` guard this issued a
  // second concurrent PATCH on the same mutation.
  it("ESC during the in-flight handoff does not issue a second completion", async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const hold = new Promise<void>((r) => {
      release = r;
    });
    const { patchCalls } = handoffSetup({ hold });
    const button = await advanceToHandoff(user);

    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    await user.keyboard("{Escape}");
    await user.keyboard("{Escape}");
    expect(patchCalls).toHaveLength(1);

    release();
    await waitFor(() => {
      expect(screen.getByTestId("location-probe").textContent).toBe("/me/sources");
    });
    expect(patchCalls).toHaveLength(1);
  });

  it("disables the handoff button while the completion is in flight", async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const hold = new Promise<void>((r) => {
      release = r;
    });
    const { patchCalls } = handoffSetup({ hold });
    const button = await advanceToHandoff(user);

    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    await user.click(button);
    expect(patchCalls).toHaveLength(1);

    // Release before returning — a held latch leaks a pending promise into the
    // rest of the file.
    release();
    await waitFor(() => {
      expect(screen.getByTestId("location-probe").textContent).toBe("/me/sources");
    });
  });
});
