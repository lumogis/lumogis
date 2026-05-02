// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { PushOptIn } from "../../../src/features/me/PushOptIn";

import { jsonResponse } from "../../helpers/jsonResponse";

const userFixture = { id: "alice", email: "a@example.com", role: "user" as const };

describe("PushOptIn", () => {
  let requestPermission!: ReturnType<typeof vi.fn>;
  let pushSubscribe!: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    Object.defineProperty(globalThis.window, "isSecureContext", {
      configurable: true,
      writable: true,
      value: true,
    });
    pushSubscribe = vi.fn().mockResolvedValue({
      endpoint: "https://push.test/leaked-path-should-not-appear-ui",
      toJSON() {
        return {
          endpoint: "https://push.test/leaked-path-should-not-appear-ui",
          keys: { p256dh: "kp", auth: "ka" },
        };
      },
    });

    vi.stubGlobal("PushManager", class PushManager {});

    requestPermission = vi.fn().mockResolvedValue("granted");
    class NotifStub {
      static permission: NotificationPermission = "default";
      static requestPermission = requestPermission;
    }
    vi.stubGlobal("Notification", NotifStub as unknown as typeof Notification);

    Object.defineProperty(globalThis.navigator, "serviceWorker", {
      configurable: true,
      value: {
        ready: Promise.resolve({
          active: {},
          scope: "/",
          pushManager: {
            getSubscription: vi.fn().mockResolvedValue(null),
            subscribe: pushSubscribe,
          },
        }),
        getRegistration: vi.fn().mockResolvedValue(null),
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function harness(fetchMock: Mock): void {
    const store = new AccessTokenStore();
    const client = new ApiClient({
      tokens: store,
      fetchImpl: fetchMock as unknown as typeof fetch,
    });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <PushOptIn />
      </AuthProvider>,
    );
  }

  it("does not call Notification.requestPermission before the user activates enable", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo) => {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/auth/me")) return jsonResponse(200, userFixture);
      if (url.includes("/vapid-public-key")) return jsonResponse(200, { public_key: "B".repeat(86) });
      if (url.includes("/notifications/subscriptions")) return jsonResponse(200, { subscriptions: [] });
      return jsonResponse(404, {});
    });
    harness(fetchMock);

    await waitFor(() => expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("vapid"))).toBe(true));

    await waitFor(() => expect(screen.getByRole("button", { name: /enable browser push/i })).toBeVisible());
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("shows server-not-configured copy on VAPID 503", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo) => {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/auth/me")) return jsonResponse(200, userFixture);
      if (url.includes("/vapid-public-key")) return jsonResponse(503, { detail: { error: "webpush_not_configured" } });
      if (url.includes("/notifications/subscriptions")) return jsonResponse(200, { subscriptions: [] });
      return jsonResponse(404, {});
    });
    harness(fetchMock);
    await waitFor(() =>
      expect(screen.getByText(/web push is not configured on this lumogis server/i)).toBeVisible(),
    );
  });

  it("when granted permission, invokes PushManager.subscribe and POST /subscribe without surfacing leaked path", async () => {
    let subscribeJson: Record<string, unknown> | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.url;
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(200, userFixture);
      if (url.includes("/vapid-public-key")) return jsonResponse(200, { public_key: "B".repeat(86) });
      if (method === "GET" && url.includes("/notifications/subscriptions"))
        return jsonResponse(200, { subscriptions: [] });
      if (method === "POST" && url.includes("/subscribe")) {
        subscribeJson = JSON.parse(init?.body as string) as Record<string, unknown>;
        return jsonResponse(201, { id: 99, already_existed: false });
      }
      return jsonResponse(404, {});
    });

    harness(fetchMock);

    await waitFor(() => expect(screen.getByRole("button", { name: /enable browser push/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /enable browser push/i }));

    await waitFor(() => expect(requestPermission).toHaveBeenCalled());
    await waitFor(() => expect(pushSubscribe).toHaveBeenCalled());
    expect(pushSubscribe.mock.calls[0]?.[0]).toMatchObject({ userVisibleOnly: true });

    await waitFor(() => expect(screen.getByText(/this browser is registered for web push/i)).toBeVisible());
    await waitFor(() => expect(subscribeJson?.endpoint).toBeTruthy());

    expect(screen.queryByText(/leaked-path/)).toBeNull();

    /** Server receives raw endpoint for wire protocol (not echoed in listing UI elsewhere). */
    expect(String(subscribeJson?.endpoint)).toContain("/leaked-path");
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/notifications/subscribe"))).toBe(true);
  });

  it("shows denied messaging and does not POST subscribe when Notification permission denied", async () => {
    requestPermission.mockResolvedValue("denied" as NotificationPermission);

    let posted = false;
    const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.url;
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(200, userFixture);
      if (url.includes("/vapid-public-key")) return jsonResponse(200, { public_key: "B".repeat(86) });
      if (method === "GET" && url.includes("/notifications/subscriptions"))
        return jsonResponse(200, { subscriptions: [] });
      if (method === "POST" && url.includes("/subscribe")) {
        posted = true;
        return jsonResponse(201, { id: 1, already_existed: false });
      }
      return jsonResponse(404, {});
    });

    harness(fetchMock);

    await waitFor(() => expect(screen.getByRole("button", { name: /enable browser push/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /enable browser push/i }));

    await waitFor(() => expect(screen.getByText(/blocked for this origin/i)).toBeTruthy());
    expect(posted).toBe(false);
  });

  it("shows redacted subscriptions; PATCH carries only touched pref; DELETE calls unsubscribe", async () => {
    const payloads: Record<string, unknown>[] = [];
    let patchUrl: string | null = null;

    const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.url;
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(200, userFixture);
      if (url.includes("/vapid-public-key")) return jsonResponse(200, { public_key: "B".repeat(86) });
      if (method === "GET" && url.includes("/notifications/subscriptions")) {
        return jsonResponse(200, {
          subscriptions: [
            {
              id: 51,
              endpoint_origin: "https://push.provider.test",
              created_at: "2026-01-01T00:00:00Z",
              last_seen_at: "2026-01-02T00:00:00Z",
              last_error: null,
              user_agent: "TestAgent",
              notify_on_signals: false,
              notify_on_shared_scope: true,
            },
          ],
        });
      }
      if (method === "PATCH") {
        patchUrl = url;
        const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : {};
        payloads.push(body);
        return jsonResponse(200, {
          id: 51,
          endpoint_origin: "https://push.provider.test",
          created_at: "2026-01-01T00:00:00Z",
          last_seen_at: "2026-01-02T00:00:00Z",
          last_error: null,
          user_agent: "TestAgent",
          notify_on_signals: Boolean(body.notify_on_signals),
          notify_on_shared_scope:
            typeof body.notify_on_shared_scope === "boolean" ? body.notify_on_shared_scope : true,
        });
      }
      if (method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return jsonResponse(404, {});
    });

    harness(fetchMock);

    await waitFor(() => expect(screen.getByText(/https:\/\/push.provider.test/i)).toBeVisible());
    expect(screen.queryByText(/super-secret-push-path/)).toBeNull();

    const sig = screen.getByRole("checkbox", { name: /notify on signals/i });
    await userEvent.click(sig);
    await waitFor(() => expect(payloads[payloads.length - 1]).toEqual({ notify_on_signals: true }));
    await waitFor(() =>
      expect(String(patchUrl)).toMatch(/\/api\/v1\/notifications\/subscriptions\/51$/),
    );

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: /remove this browser/i }));
    expect(
      fetchMock.mock.calls.some((c) => {
        const innit = c[1] as RequestInit | undefined;
        return String(c[0]).includes("/subscriptions/51") && innit?.method === "DELETE";
      }),
    ).toBe(true);
    confirmSpy.mockRestore();
  });
});
