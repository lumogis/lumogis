// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSearchSettingsView } from "../../../src/features/admin/AdminSearchSettingsView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };

function settingsBody(overrides: Record<string, unknown> = {}) {
  return {
    reranker_enabled: false,
    reranker_backend_live: "none",
    reranker_pending_restart: false,
    ...overrides,
  };
}

function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <AdminSearchSettingsView />
    </AuthProvider>,
  );
}

describe("AdminSearchSettingsView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("enables the reranker and saves via PUT /settings with a RAM warning", async () => {
    const bodies: unknown[] = [];
    let getCount = 0;
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(200, {
          meta: { stack_control_reachable: true, overall_status: "ok", generated_at: "t", cache_age_sec: 0 },
          services: [],
          storage: [],
          ollama: [],
          warnings: [],
        });
      }
      if (u.endsWith("/settings") && init?.method === "PUT") {
        bodies.push(JSON.parse(String(init.body)));
        return jsonResponse(
          200,
          settingsBody({ reranker_enabled: true, reranker_pending_restart: true }),
        );
      }
      if (u.endsWith("/settings")) {
        getCount += 1;
        return jsonResponse(200, settingsBody());
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderView(fetchImpl);

    const save = await screen.findByRole("button", { name: "Save retrieval settings" });
    expect((save as HTMLButtonElement).disabled).toBe(true);

    const user = userEvent.setup();
    await user.click(screen.getByLabelText(/BGE reranker/i));

    expect(screen.getByRole("note").textContent).toMatch(/memory pressure/i);
    expect((save as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(/Unsaved change/i)).toBeTruthy();

    await user.click(save);

    await waitFor(() => expect(bodies.length).toBe(1));
    expect(bodies[0]).toMatchObject({ reranker_enabled: true });

    expect(await screen.findByText(/Change pending — restart orchestrator/i)).toBeTruthy();
    expect(getCount).toBe(1);
  });

  it("shows pending banner when desired differs from live backend", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(200, {
          meta: { stack_control_reachable: false, overall_status: "ok", generated_at: "t", cache_age_sec: 0 },
          services: [],
          storage: [],
          ollama: [],
          warnings: [],
        });
      }
      if (u.endsWith("/settings") && (!init?.method || init.method === "GET")) {
        return jsonResponse(
          200,
          settingsBody({
            reranker_enabled: true,
            reranker_backend_live: "none",
            reranker_pending_restart: true,
          }),
        );
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderView(fetchImpl);

    expect(await screen.findByText(/Change pending — restart orchestrator/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Save & restart stack/i })).toBeNull();
  });

  it("hides Save & restart when stack-control is unreachable and shows manual guidance after save", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(200, {
          meta: { stack_control_reachable: false, overall_status: "ok", generated_at: "t", cache_age_sec: 0 },
          services: [],
          storage: [],
          ollama: [],
          warnings: [],
        });
      }
      if (u.endsWith("/settings") && init?.method === "PUT") {
        return jsonResponse(
          200,
          settingsBody({ reranker_enabled: true, reranker_pending_restart: true }),
        );
      }
      if (u.endsWith("/settings")) {
        return jsonResponse(200, settingsBody());
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderView(fetchImpl);

    const user = userEvent.setup();
    await user.click(await screen.findByLabelText(/BGE reranker/i));
    expect(screen.queryByRole("button", { name: /Save & restart stack/i })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Save retrieval settings" }));
    expect(await screen.findByText(/Restart the Lumogis orchestrator process manually/i)).toBeTruthy();
  });

  it("save without restart does not POST /settings/restart", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        return jsonResponse(200, {
          meta: { stack_control_reachable: true, overall_status: "ok", generated_at: "t", cache_age_sec: 0 },
          services: [],
          storage: [],
          ollama: [],
          warnings: [],
        });
      }
      if (u.endsWith("/settings/restart")) return jsonResponse(500, { detail: "unexpected" });
      if (u.endsWith("/settings") && init?.method === "PUT") {
        return jsonResponse(
          200,
          settingsBody({ reranker_enabled: true, reranker_pending_restart: true }),
        );
      }
      if (u.endsWith("/settings")) {
        return jsonResponse(200, settingsBody());
      }
      return jsonResponse(404, {});
    });
    const fetchImpl = fetchMock as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderView(fetchImpl);

    const user = userEvent.setup();
    await user.click(await screen.findByLabelText(/BGE reranker/i));
    await user.click(screen.getByRole("button", { name: "Save retrieval settings" }));
    await screen.findByText(/Change pending — restart orchestrator/i);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).endsWith("/settings/restart") && init?.method === "POST",
      ),
    ).toBe(false);
  });
});
