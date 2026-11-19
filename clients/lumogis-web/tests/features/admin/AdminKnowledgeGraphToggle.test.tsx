// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-989 — Admin → Knowledge graph: Turn on / Turn off on a Release install.
// The server computes `turn_on` / `turn_off`; these tests only check that the page
// follows them, asks for the confirmations, and polls while Core restarts.

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import type { AdminKnowledgeGraphStatus, KnowledgeGraphState } from "../../../src/api/knowledgeGraph";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { KNOWLEDGE_GRAPH_POLL_MS } from "../../../src/api/knowledgeGraph";
import { AdminKnowledgeGraphView } from "../../../src/features/admin/AdminKnowledgeGraphView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { GIB, kgStatus } from "../../helpers/knowledgeGraphStatus";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };
const BASE = "/api/v1/admin/knowledge-graph";

type Call = { method: string; path: string; body: unknown };
type Reply = AdminKnowledgeGraphStatus | "error";

/** GET answers come from `next()` (called per request); `writes` answers the POSTs. */
function serve(next: () => Reply, writes: Record<string, () => Response> = {}) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = String(input);
    const method = init?.method ?? "GET";
    const path = new URL(u, "http://localhost").pathname;
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === "/api/v1/auth/me") return jsonResponse(200, adminUser);
    if (path === BASE && method === "GET") {
      const r = next();
      if (r === "error") throw new TypeError("Failed to fetch");
      return jsonResponse(200, r);
    }
    const w = writes[`${method} ${path}`];
    if (w) return w();
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
  globalThis.fetch = fetchImpl;
  return { fetchImpl, calls };
}

function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  render(
    <MemoryRouter>
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminKnowledgeGraphView />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const gets = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.path === BASE).length;
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");
const rel = (state: KnowledgeGraphState, o: Parameters<typeof kgStatus>[1] = {}) =>
  kgStatus(state, { install: "release", ...o });

describe("AdminKnowledgeGraphView — Turn on / Turn off", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    seedSignedInDevice("admin1");
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // One case per row of the availability table: the buttons follow the server.
  const ROWS: [KnowledgeGraphState, string[]][] = [
    ["off", ["Turn on"]],
    ["off_low_memory", ["Turn on"]],
    ["start_failed", ["Retry turn on", "Turn off"]],
    ["stop_failed", ["Retry turn off"]],
    ["starting", []],
    ["stopping", []],
    ["on", ["Turn off"]],
    ["service_unreachable", ["Turn off"]],
    ["service_wiring_failed", ["Turn off"]],
  ];
  for (const [state, expected] of ROWS) {
    it(`${state}: buttons are ${expected.join(" + ") || "none"}`, async () => {
      const { fetchImpl } = serve(() => rel(state));
      renderView(fetchImpl);
      await waitFor(() => expect(document.querySelector("[data-tone]")).toBeTruthy());
      const labels = ["Turn on", "Retry turn on", "Turn off", "Retry turn off"];
      const present = labels.filter((l) => screen.queryByRole("button", { name: l }) !== null);
      expect(present.sort()).toEqual([...expected].sort());
    });
  }

  it("a checkout has no button (the server says blocked) and shows the written steps", async () => {
    const { fetchImpl } = serve(() => kgStatus("off", { install: "checkout" }));
    renderView(fetchImpl);
    await screen.findByText("The knowledge graph is off.");
    expect(screen.queryByRole("button", { name: /Turn o/ })).toBeNull();
    expect(screen.getByRole("heading", { name: "How to turn it on (source checkout)" })).toBeTruthy();
  });

  it("a blocked install says why, in the server's words", async () => {
    const { fetchImpl } = serve(() =>
      rel("off", { turnOn: { available: false, block: "compose_too_old" } }),
    );
    renderView(fetchImpl);
    expect(await screen.findByText(/does not include the knowledge graph/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn on" })).toBeNull();
  });

  it("shows the offer heading when Turn on is available", async () => {
    const { fetchImpl } = serve(() => rel("off"));
    renderView(fetchImpl);
    expect(await screen.findByRole("heading", { name: "Add the knowledge graph" })).toBeTruthy();
  });

  it("Turn on posts confirm_low_memory:false at once when memory is fine", async () => {
    let on = false;
    const { fetchImpl, calls } = serve(() => (on ? rel("starting", { turnOn: undefined }) : rel("off")), {
      [`POST ${BASE}/enable`]: () => {
        on = true;
        return jsonResponse(202, { state: "starting" });
      },
    });
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    expect(await screen.findByText("Starting the knowledge graph…")).toBeTruthy();
    expect(posts(calls)).toEqual([{ method: "POST", path: `${BASE}/enable`, body: { confirm_low_memory: false } }]);
  });

  it("low memory: nothing is sent until the admin ticks the box and confirms", async () => {
    let on = false;
    const { fetchImpl, calls } = serve(
      () => (on ? rel("starting") : rel("off_low_memory", { total: 8 * GIB, enough: false })),
      {
        [`POST ${BASE}/enable`]: () => {
          on = true;
          return jsonResponse(202, { state: "starting" });
        },
      },
    );
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    const group = await screen.findByRole("group", { name: "Confirm turning on" });
    expect(group.textContent).toContain("about 8.0 GB");
    expect(group.textContent).toContain("Settings → Resources");
    expect(group.textContent).toContain("about 1 GB");
    const confirm = screen.getByRole("button", { name: "Turn on anyway" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(posts(calls)).toHaveLength(0);
    fireEvent.click(screen.getByRole("checkbox", { name: "I understand, turn it on anyway" }));
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await screen.findByText("Starting the knowledge graph…");
    expect(posts(calls)).toEqual([{ method: "POST", path: `${BASE}/enable`, body: { confirm_low_memory: true } }]);
  });

  it("Cancel on the low-memory confirmation sends nothing", async () => {
    const { fetchImpl, calls } = serve(() => rel("off_low_memory", { enough: false }));
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Confirm turning on" })).toBeNull();
    expect(posts(calls)).toHaveLength(0);
  });

  it("Turn off asks first, then posts", async () => {
    let off = false;
    const { fetchImpl, calls } = serve(() => (off ? rel("stopping") : rel("on")), {
      [`POST ${BASE}/disable`]: () => {
        off = true;
        return jsonResponse(202, { state: "stopping" });
      },
    });
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    const group = await screen.findByRole("alertdialog");
    expect(group.textContent).toContain("Your graph data is kept");
    expect(posts(calls)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Yes, turn off" }));
    await screen.findByText("Stopping the knowledge graph…");
    expect(posts(calls)).toEqual([{ method: "POST", path: `${BASE}/disable`, body: {} }]);
  });

  it("a 409 busy shows a message and refreshes the status", async () => {
    const { fetchImpl, calls } = serve(() => rel("off"), {
      [`POST ${BASE}/enable`]: () => jsonResponse(409, { detail: "busy" }),
    });
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    expect(await screen.findByText(/already starting or stopping/)).toBeTruthy();
    await waitFor(() => expect(gets(calls)).toBeGreaterThanOrEqual(2));
  });

  it("a 409 needs_confirmation opens the confirmation", async () => {
    const { fetchImpl } = serve(() => rel("off"), {
      [`POST ${BASE}/enable`]: () => jsonResponse(409, { detail: "needs_confirmation" }),
    });
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("group", { name: "Confirm turning on" })).toBeTruthy();
  });

  it("start_failed names the code's text, says the setting is saved and offers Retry and Turn off", async () => {
    const { fetchImpl } = serve(() => rel("start_failed", { failed: "timeout" }));
    renderView(fetchImpl);
    expect(await screen.findByText("The knowledge graph did not start.")).toBeTruthy();
    expect(screen.getByText(/Starting took too long/)).toBeTruthy();
    expect(screen.getByText(/The setting is saved/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry turn on" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Turn off" })).toBeTruthy();
  });

  it("stop_failed offers Retry turn off only", async () => {
    const { fetchImpl } = serve(() => rel("stop_failed", { failed: "compose_failed" }));
    renderView(fetchImpl);
    expect(await screen.findByText("The knowledge graph did not stop.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry turn off" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /turn on/i })).toBeNull();
  });

  describe("polling", () => {
    it("polls every 5 s while starting, through at least two failed requests, and stops on on", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const script: Reply[] = [
        rel("starting"), // first load
        "error", // Core restarting: two failures in a row
        "error",
        rel("starting"),
        rel("on"),
        rel("on"),
      ];
      let i = 0;
      const { fetchImpl, calls } = serve(() => script[Math.min(i++, script.length - 1)]);
      renderView(fetchImpl);
      await screen.findByText("Starting the knowledge graph…");
      expect(gets(calls)).toBe(1);
      expect(KNOWLEDGE_GRAPH_POLL_MS).toBe(5000);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4900);
      });
      expect(gets(calls)).toBe(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200);
      });
      expect(gets(calls)).toBe(2); // failed
      // The last answer stays on screen, with a note that Lumogis is restarting.
      expect(await screen.findByText(/Lumogis is restarting/)).toBeTruthy();
      expect(screen.getByText("Starting the knowledge graph…")).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(gets(calls)).toBe(3); // failed again: polling kept going
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(gets(calls)).toBe(4); // answered: still starting
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      await screen.findByText("The knowledge graph is on.");
      expect(gets(calls)).toBe(5);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(gets(calls)).toBe(5); // on: polling stopped
    });

    it("polls while stopping and stops on off", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const script: Reply[] = [rel("stopping"), "error", rel("off")];
      let i = 0;
      const { fetchImpl, calls } = serve(() => script[Math.min(i++, script.length - 1)]);
      renderView(fetchImpl);
      await screen.findByText("Stopping the knowledge graph…");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5100);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      await screen.findByText("The knowledge graph is off.");
      const n = gets(calls);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(gets(calls)).toBe(n);
    });

    it("does not poll in a settled state", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const { fetchImpl, calls } = serve(() => rel("on"));
      renderView(fetchImpl);
      await screen.findByText("The knowledge graph is on.");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(gets(calls)).toBe(1);
    });
  });
});
