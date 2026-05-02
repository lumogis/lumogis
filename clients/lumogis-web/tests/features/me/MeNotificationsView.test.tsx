// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Read-only Notifications view — GET /api/v1/me/notifications only.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeNotificationsView } from "../../../src/features/me/MeNotificationsView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MeNotificationsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const user = { id: "u1", email: "u@home.lan", role: "user" as const };

  const samplePayload = {
    channels: [
      {
        connector: "ntfy",
        label: "ntfy",
        description: "ntfy push-notification connector.",
        configured: true,
        active_tier: "user",
        user_credential_present: true,
        household_credential_available: false,
        system_credential_available: false,
        env_fallback_available: false,
        url: null,
        url_configured: null,
        topic_configured: null,
        token_configured: null,
        updated_at: "2026-04-25T12:00:00Z",
        key_version: 1,
        subscription_count: null,
        push_service_configured: null,
        status: "configured",
        why_not_available: null,
      },
      {
        connector: "web_push",
        label: "Web Push",
        description: "Browser notifications.",
        configured: false,
        active_tier: "none",
        user_credential_present: false,
        household_credential_available: false,
        system_credential_available: false,
        env_fallback_available: false,
        url: null,
        url_configured: null,
        topic_configured: null,
        token_configured: null,
        updated_at: null,
        key_version: null,
        subscription_count: 0,
        push_service_configured: true,
        status: "not_configured",
        why_not_available: "No browser push subscriptions for this account.",
      },
    ],
    summary: {
      total: 2,
      configured: 1,
      not_configured: 1,
      by_active_tier: { user: 1, none: 1 },
    },
  };

  it("calls GET /api/v1/me/notifications and renders summary", async () => {
    let url: string | null = null;
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/notifications/vapid-public-key")) {
        return jsonResponse(503, { detail: { error: "webpush_not_configured" } });
      }
      if (/\/api\/v1\/notifications\/subscriptions$/.test(u)) return jsonResponse(200, { subscriptions: [] });
      if (
        u.includes("/api/v1/me/notifications") &&
        (_init?.method === "GET" || _init?.method === undefined)
      ) {
        url = u;
        return jsonResponse(200, samplePayload);
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeNotificationsView />
      </AuthProvider>,
    );

    await waitFor(() => expect(url).toContain("/api/v1/me/notifications"));
    await waitFor(() => expect(screen.getByLabelText(/Total channels: 2/)).toBeInTheDocument());
    expect(screen.getByLabelText(/Configured channels: 1/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Not configured: 1/)).toBeInTheDocument();
  });

  it("renders channels read-only — no send/save/reveal", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/notifications/vapid-public-key")) {
        return jsonResponse(503, { detail: { error: "webpush_not_configured" } });
      }
      if (/\/api\/v1\/notifications\/subscriptions$/.test(u)) return jsonResponse(200, { subscriptions: [] });
      if (u.includes("/api/v1/me/notifications")) return jsonResponse(200, samplePayload);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeNotificationsView />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getAllByText("ntfy").length).toBeGreaterThanOrEqual(1));
    expect(screen.getByText("web_push")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send|test push|save|reveal/i })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("renders error state on API failure", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/notifications")) return jsonResponse(500, { detail: "boom" });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeNotificationsView />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
