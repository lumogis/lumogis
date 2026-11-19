// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Me Notifications view — status façade + editable prefs matrix.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeNotificationsView } from "../../../src/features/me/MeNotificationsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

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
      paused: 0,
      by_active_tier: { user: 1, none: 1 },
    },
  };

  const samplePrefs = {
    types: [
      {
        notification_type: "signal_received",
        tier: "informational",
        channels: [
          { channel: "ntfy", enabled: true, effective: true, mutable: true, tier_default: true },
          { channel: "web_push", enabled: true, effective: true, mutable: true, tier_default: true },
          { channel: "in_app", enabled: true, effective: true, mutable: true, tier_default: true },
        ],
      },
      {
        notification_type: "consolidation_done",
        tier: "background",
        channels: [
          { channel: "ntfy", enabled: false, effective: false, mutable: false, tier_default: false },
          { channel: "web_push", enabled: false, effective: false, mutable: false, tier_default: false },
          { channel: "in_app", enabled: true, effective: true, mutable: true, tier_default: true },
        ],
      },
    ],
    timezone: null,
    quiet_hours_start: null,
    quiet_hours_end: null,
  };

  function mockFetch(
    fetchImpl: (input: RequestInfo, init?: RequestInit) => Promise<Response>,
  ) {
    return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/notifications/vapid-public-key")) {
        return jsonResponse(503, { detail: { error: "webpush_not_configured" } });
      }
      if (/\/api\/v1\/notifications\/subscriptions$/.test(u)) return jsonResponse(200, { subscriptions: [] });
      if (u.includes("/api/v1/me/notification-preferences") && init?.method !== "PATCH") {
        return jsonResponse(200, samplePrefs);
      }
      return fetchImpl(input, init);
    });
  }

  it("calls GET /api/v1/me/notifications and renders summary", async () => {
    let url: string | null = null;
    const fetchImpl = mockFetch(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
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
    await waitFor(() =>
      expect(screen.getByText(/2 channels, 1 set up, 1 not set up, 0 paused/)).toBeInTheDocument(),
    );
  });

  it("renders channels status table — no send/save/reveal on status rows", async () => {
    const fetchImpl = mockFetch(async (input: RequestInfo) => {
      const u = String(input);
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

  it("configured filter hides paused channels", async () => {
    const pausedPayload = {
      channels: [
        {
          connector: "ntfy",
          label: "ntfy",
          description: "d",
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
          status: "paused",
          why_not_available: "Paused.",
        },
        {
          connector: "web_push",
          label: "Web Push",
          description: "",
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
          why_not_available: "No subscriptions.",
        },
      ],
      summary: {
        total: 2,
        configured: 0,
        not_configured: 1,
        paused: 1,
        by_active_tier: { user: 1, none: 1 },
      },
    };

    const fetchImpl = mockFetch(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/me/notifications")) return jsonResponse(200, pausedPayload);
      return jsonResponse(404, {});
    });

    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeNotificationsView />
      </AuthProvider>,
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Details" }));
    expect(await screen.findByRole("row", { name: /ntfy.*Paused/s })).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Filter channels" }));
    await user.click(await screen.findByRole("option", { name: "Configured only" }));
    await waitFor(() => expect(screen.queryByRole("row", { name: /ntfy.*Paused/s })).toBeNull());
  });

  it("renders preference matrix with checkboxes", async () => {
    const fetchImpl = mockFetch(async (input: RequestInfo) => {
      const u = String(input);
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

    await waitFor(() => expect(screen.getByLabelText("Notification preference matrix")).toBeInTheDocument());
    expect(screen.getByRole("checkbox", { name: "Signal received — ntfy" })).toBeInTheDocument();
    expect(screen.getByText("Not available yet")).toBeInTheDocument();
  });

  it("toggle calls PATCH with optimistic update", async () => {
    let patched = false;
    const fetchImpl = mockFetch(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/me/notifications")) return jsonResponse(200, samplePayload);
      if (u.includes("/api/v1/me/notification-preferences") && init?.method === "PATCH") {
        patched = true;
        return jsonResponse(200, samplePrefs);
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

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Signal received — ntfy" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "Signal received — ntfy" }));
    await waitFor(() => expect(patched).toBe(true));
  });

  it("out-of-tier cell disabled with explanation", async () => {
    const fetchImpl = mockFetch(async (input: RequestInfo) => {
      const u = String(input);
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

    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Consolidation done — ntfy" })).toHaveAttribute(
        "aria-disabled",
        "true",
      ),
    );
  });

  it("renders quiet hours controls seeded from the current preferences", async () => {
    const seededPrefs = { ...samplePrefs, timezone: "Europe/Berlin", quiet_hours_start: "22:00:00", quiet_hours_end: "07:00:00" };
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/notifications/vapid-public-key")) {
        return jsonResponse(503, { detail: { error: "webpush_not_configured" } });
      }
      if (/\/api\/v1\/notifications\/subscriptions$/.test(u)) return jsonResponse(200, { subscriptions: [] });
      if (u.includes("/api/v1/me/notification-preferences") && init?.method !== "PATCH") {
        return jsonResponse(200, seededPrefs);
      }
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

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Timezone" })).toHaveTextContent("Europe/Berlin"),
    );
    expect(screen.getByLabelText("From")).toHaveValue("22:00");
    expect(screen.getByLabelText("Until")).toHaveValue("07:00");
    expect(screen.getByRole("button", { name: "Clear quiet hours" })).toBeInTheDocument();
  });

  it("does not show a clear button when no quiet-hours window is set", async () => {
    const fetchImpl = mockFetch(async (input: RequestInfo) => {
      const u = String(input);
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

    await waitFor(() => expect(screen.getByRole("combobox", { name: "Timezone" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Clear quiet hours" })).toBeNull();
  });

  it("changing the timezone PATCHes immediately", async () => {
    let patchedBody: unknown = null;
    const fetchImpl = mockFetch(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/me/notifications")) return jsonResponse(200, samplePayload);
      if (u.includes("/api/v1/me/notification-preferences") && init?.method === "PATCH") {
        patchedBody = JSON.parse(String(init.body));
        return jsonResponse(200, { ...samplePrefs, timezone: "Asia/Tokyo" });
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

    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Timezone" })).toBeInTheDocument());
    await user.click(screen.getByRole("combobox", { name: "Timezone" }));
    await user.click(await screen.findByRole("option", { name: "Asia/Tokyo" }));
    await waitFor(() => expect(patchedBody).toEqual({ timezone: "Asia/Tokyo" }));
  });

  it("blurring the quiet-hours start time PATCHes the new value", async () => {
    let patchedBody: unknown = null;
    const fetchImpl = mockFetch(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/me/notifications")) return jsonResponse(200, samplePayload);
      if (u.includes("/api/v1/me/notification-preferences") && init?.method === "PATCH") {
        patchedBody = JSON.parse(String(init.body));
        return jsonResponse(200, { ...samplePrefs, quiet_hours_start: "22:00:00" });
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

    await waitFor(() => expect(screen.getByLabelText("From")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "22:00" } });
    fireEvent.blur(screen.getByLabelText("From"));
    await waitFor(() => expect(patchedBody).toEqual({ quiet_hours_start: "22:00" }));
  });

  it("clicking Clear quiet hours clears both start and end", async () => {
    const seededPrefs = { ...samplePrefs, quiet_hours_start: "22:00:00", quiet_hours_end: "07:00:00" };
    let patchedBody: unknown = null;
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/notifications/vapid-public-key")) {
        return jsonResponse(503, { detail: { error: "webpush_not_configured" } });
      }
      if (/\/api\/v1\/notifications\/subscriptions$/.test(u)) return jsonResponse(200, { subscriptions: [] });
      if (u.includes("/api/v1/me/notification-preferences") && init?.method === "PATCH") {
        patchedBody = JSON.parse(String(init.body));
        return jsonResponse(200, samplePrefs);
      }
      if (u.includes("/api/v1/me/notification-preferences")) return jsonResponse(200, seededPrefs);
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

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Clear quiet hours" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear quiet hours" }));
    await waitFor(() =>
      expect(patchedBody).toEqual({ quiet_hours_start: null, quiet_hours_end: null }),
    );
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
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows the ntfy steps in plain words and keeps tier names out of the main view", async () => {
    const fetchImpl = mockFetch(async (input: RequestInfo) => {
      const u = String(input);
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

    expect(await screen.findByText("Install the ntfy app on your phone.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connected services" })).toHaveAttribute("href", "/settings/connections");
    // Tiers, ids and VAPID sit behind the closed Details.
    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("cell", { name: "web_push" })).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Details" }));
    expect(await screen.findByRole("cell", { name: "web_push" })).toBeInTheDocument();
    expect(screen.getByText("Signal received: informational")).toBeInTheDocument();
  });

  it("shows an error with retry when the choices cannot load", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/notifications/vapid-public-key")) {
        return jsonResponse(503, { detail: { error: "webpush_not_configured" } });
      }
      if (/\/api\/v1\/notifications\/subscriptions$/.test(u)) return jsonResponse(200, { subscriptions: [] });
      if (u.includes("/api/v1/me/notification-preferences") && init?.method !== "PATCH") {
        return jsonResponse(500, { detail: "boom" });
      }
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

    expect(await screen.findByText(/couldn't load your notification choices/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
