// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-989 — the System status offer line: admin-only, only when Turn on is
// available and the machine is not short of memory, dismissal kept per device,
// and the same React Query key as the Knowledge graph page (one status call).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import type { AdminKnowledgeGraphStatus } from "../../src/api/knowledgeGraph";
import { AccessTokenStore } from "../../src/api/tokens";
import { AuthProvider } from "../../src/auth/AuthProvider";
import { GraphOfferLine } from "../../src/components/GraphOfferLine";
import { GRAPH_OFFER_DISMISS_KEY } from "../../src/features/admin/knowledgeGraphCopy";
import { jsonResponse } from "../helpers/jsonResponse";
import { GIB, kgStatus } from "../helpers/knowledgeGraphStatus";
import { seedSignedInDevice } from "../helpers/signedInDevice";

const BASE = "/api/v1/admin/knowledge-graph";

function mount(status: AdminKnowledgeGraphStatus | 403, role: "admin" | "user" = "admin") {
  const calls: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://localhost").pathname;
    calls.push(path);
    if (path === "/api/v1/auth/me") return jsonResponse(200, { id: "u1", email: "a@home.lan", role });
    if (path === BASE) return status === 403 ? jsonResponse(403, { detail: "admin required" }) : jsonResponse(200, status);
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
  globalThis.fetch = fetchImpl;
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  render(
    <MemoryRouter>
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <GraphOfferLine />
      </AuthProvider>
    </MemoryRouter>,
  );
  return calls;
}

const offerable = () => kgStatus("off", { install: "release" });

describe("GraphOfferLine", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    seedSignedInDevice("u1");
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    localStorage.removeItem(GRAPH_OFFER_DISMISS_KEY);
    vi.restoreAllMocks();
  });

  it("shows the line with a link to the page when the graph can be turned on", async () => {
    mount(offerable());
    const line = await screen.findByTestId("graph-offer-line");
    expect(line.textContent).toContain("knowledge graph");
    expect(screen.getByRole("link", { name: "Open Knowledge graph" }).getAttribute("href")).toBe(
      "/settings/knowledge-graph",
    );
  });

  it("reads the page's query key: one status call, not two", async () => {
    const calls = mount(offerable());
    await screen.findByTestId("graph-offer-line");
    expect(calls.filter((c) => c === BASE)).toHaveLength(1);
  });

  it.each([
    ["on", kgStatus("on", { install: "release" })],
    ["starting", kgStatus("starting", { install: "release" })],
    ["start_failed", kgStatus("start_failed", { install: "release" })],
    ["a checkout", kgStatus("off", { install: "checkout" })],
    ["Turn on blocked", kgStatus("off", { install: "release", turnOn: { available: false, block: "env_unwritable" } })],
    ["memory short", kgStatus("off_low_memory", { install: "release", total: 8 * GIB, enough: false })],
    ["off with memory short", kgStatus("off", { install: "release", enough: false })],
  ])("shows nothing when %s", async (_name, status) => {
    const calls = mount(status);
    await waitFor(() => expect(calls.filter((c) => c === BASE)).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("graph-offer-line")).toBeNull();
  });

  it("shows when the memory verdict is unknown", async () => {
    mount(kgStatus("off", { install: "release", total: null, enough: null }));
    expect(await screen.findByTestId("graph-offer-line")).toBeTruthy();
  });

  it("shows nothing when the status is refused (a member would get 403)", async () => {
    const calls = mount(403, "user");
    await waitFor(() => expect(calls).toContain(BASE));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("graph-offer-line")).toBeNull();
  });

  it("Dismiss hides it and is remembered on this device", async () => {
    mount(offerable());
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("graph-offer-line")).toBeNull();
    expect(localStorage.getItem(GRAPH_OFFER_DISMISS_KEY)).toBe("1");
  });

  it("a dismissed line stays hidden after a reload, and makes no status call", async () => {
    localStorage.setItem(GRAPH_OFFER_DISMISS_KEY, "1");
    const calls = mount(offerable());
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("graph-offer-line")).toBeNull();
    expect(calls).not.toContain(BASE);
  });
});
