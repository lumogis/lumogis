// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import type { AdminKnowledgeGraphStatus } from "../../../src/api/knowledgeGraph";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminKnowledgeGraphView } from "../../../src/features/admin/AdminKnowledgeGraphView";
import { KG_DOCS_URL } from "../../../src/features/admin/knowledgeGraphCopy";
import { jsonResponse } from "../../helpers/jsonResponse";
import { kgStatus } from "../../helpers/knowledgeGraphStatus";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };
const GIB = 1024 ** 3;

function payload(
  state: AdminKnowledgeGraphStatus["state"],
  memory: Partial<AdminKnowledgeGraphStatus["memory"]> = {},
): AdminKnowledgeGraphStatus {
  const base = kgStatus(state);
  return { ...base, memory: { ...base.memory, ...memory } };
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

function serve(status: number, body: unknown): typeof fetch {
  const fetchImpl = vi.fn(async (input) => {
    const u = String(input);
    if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
    if (u.includes("/api/v1/admin/knowledge-graph")) return jsonResponse(status, body);
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
  globalThis.fetch = fetchImpl;
  return fetchImpl;
}

describe("AdminKnowledgeGraphView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    // LUM-886: a real AuthProvider wipes the query cache on the first sign-in of a device;
    // mark it reconciled so the view's own query survives.
    seedSignedInDevice("admin1");
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows a busy section while loading", () => {
    const fetchImpl = vi.fn(async (input) => {
      if (String(input).includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      return new Promise<Response>(() => {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);
    expect(document.querySelector("[aria-busy='true']")).toBeTruthy();
    expect(screen.getByText("Loading the knowledge graph…")).toBeTruthy();
  });

  it("shows an admin-only message on 403", async () => {
    renderView(serve(403, { error: "forbidden", detail: "admin required" }));
    expect(await screen.findByText("Admin role required for the knowledge graph page.")).toBeTruthy();
  });

  it("off on a checkout: offers the steps, the Release hint and the docs link, and no button", async () => {
    renderView(serve(200, payload("off")));
    const headline = await screen.findByText("The knowledge graph is off.");
    expect(headline.getAttribute("data-tone")).toBe("info");
    expect(screen.getByRole("heading", { name: "How to turn it on (source checkout)" })).toBeTruthy();
    const pre = document.querySelector("pre");
    expect(pre?.textContent).toContain("GRAPH_MODE=service");
    expect(screen.getByText(/Then this page has a Turn on button instead of these steps/)).toBeTruthy();
    expect(screen.queryByText(/cannot be turned on there yet/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn on" })).toBeNull();
    const link = screen.getByRole("link", { name: "Turn on the knowledge graph (full steps)" });
    expect(link.getAttribute("href")).toBe(KG_DOCS_URL);
  });

  it("on: says so and shows no steps or Release note", async () => {
    renderView(serve(200, payload("on")));
    const headline = await screen.findByText("The knowledge graph is on.");
    expect(headline.getAttribute("data-tone")).toBe("ok");
    expect(screen.queryByRole("heading", { name: /How to turn it on/ })).toBeNull();
    expect(screen.queryByText(/Then this page has a Turn on button/)).toBeNull();
  });

  it("service_unreachable: warns and links to Diagnostics", async () => {
    renderView(serve(200, payload("service_unreachable")));
    const headline = await screen.findByText(/cannot reach the graph service/);
    expect(headline.getAttribute("data-tone")).toBe("warn");
    expect(screen.getByText("Last check: timed out.")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open Diagnostics" });
    expect(link.getAttribute("href")).toBe("/settings/diagnostics");
  });

  it("off_low_memory: no steps, and the memory line says not enough", async () => {
    renderView(
      serve(200, payload("off_low_memory", { total_bytes: Math.round(7.6 * GIB), enough: false })),
    );
    expect(await screen.findByText(/That is not enough/)).toBeTruthy();
    expect(screen.getByText(/does not have enough memory for it/)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /How to turn it on/ })).toBeNull();
  });

  it("shows a retry on a failed load", async () => {
    renderView(serve(500, { detail: "boom" }));
    expect(await screen.findByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("turning off asks first: Cancel does nothing, confirming calls disable", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/knowledge-graph/disable")) {
        calls.push(`${init?.method} disable`);
        return jsonResponse(202, { state: "stopping" });
      }
      if (u.includes("/api/v1/admin/knowledge-graph")) return jsonResponse(200, kgStatus("on", { install: "release" }));
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Turn off" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toMatch(/Your graph data is kept/);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(calls).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Turn off" }));
    await user.click(await screen.findByRole("button", { name: "Yes, turn off" }));
    await waitFor(() => expect(calls).toEqual(["POST disable"]));
  });
});
