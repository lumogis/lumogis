// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-331 — Admin → Secure address: one render per state, the address
// confirmation, Turn off, Check on this device, and 3 s polling (fake timers).

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import type { SecureAddressStatus } from "../../../src/api/secureAddress";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSecureAddressView } from "../../../src/features/admin/AdminSecureAddressView";
import {
  DETECTING_TEXT,
  ERROR_TEXT,
  NAME_NEW_CHECK_FAILED,
  NAME_NEW_TEXT,
} from "../../../src/features/admin/secureAddressCopy";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };
const NAME = "kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app";
const URL_ = `https://${NAME}`;

function status(over: Partial<SecureAddressStatus> = {}): SecureAddressStatus {
  return {
    state: "off",
    name: null,
    url: null,
    lan_address: null,
    lan_address_source: null,
    suggested_address: null,
    detection_unavailable_reason: null,
    certificate_valid_until: null,
    resolver: "unknown",
    error_code: null,
    retry_after_seconds: null,
    name_changed: false,
    name_new_until: null,
    sign_in_enabled: true,
    warnings: [],
    ...over,
  };
}

const READY = status({
  state: "ready",
  name: NAME,
  url: URL_,
  lan_address: "192.168.1.20",
  lan_address_source: "detected",
  certificate_valid_until: "2027-02-14T12:00:00Z",
  resolver: "ok",
});

type Call = { method: string; path: string; body: unknown };

/** Serves GET from `next()` (called per request), and records every call. */
function serve(next: () => SecureAddressStatus, writes: Record<string, () => Response | Promise<Response>> = {}) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = String(input);
    const method = init?.method ?? "GET";
    const path = new URL(u, "http://localhost").pathname;
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === "/api/v1/auth/me") return jsonResponse(200, adminUser);
    if (u.startsWith(URL_)) return new Response(null, { status: 200 });
    if (path.startsWith("/api/v1/admin/secure-address")) {
      if (method === "GET") return jsonResponse(200, next());
      const w = writes[`${method} ${path}`];
      if (w) return w();
    }
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
        <AdminSecureAddressView />
      </AuthProvider>
    </MemoryRouter>,
  );
}

/** A promise the test settles by hand, to hold a request in flight. */
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const detectPosts = (calls: Call[]) =>
  calls.filter((c) => c.method === "POST" && c.path === "/api/v1/admin/secure-address/detect")
    .length;

const ADDRESS_LABEL = "This computer's address on your home network";
const CONFIRM_LABEL = "Yes, my phone and laptop open Lumogis at this address.";
const addressInput = () => screen.getByLabelText(ADDRESS_LABEL) as HTMLInputElement;
// Base UI checkbox: a role element, not an <input>; its state is aria-checked.
const confirmBox = () => screen.getByRole("checkbox", { name: CONFIRM_LABEL });
const isTicked = (el: HTMLElement) => el.getAttribute("aria-checked") === "true";
const settle = () => act(async () => {});

const statusGets = (calls: Call[]) =>
  calls.filter((c) => c.method === "GET" && c.path === "/api/v1/admin/secure-address").length;

describe("AdminSecureAddressView", () => {
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

  it("shows an admin-only message on 403", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      return jsonResponse(403, { detail: "admin required" });
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;
    renderView(fetchImpl);
    expect(await screen.findByText("Admin role required for the secure address page.")).toBeTruthy();
  });

  it("off: prefilled suggestion still needs confirming before Turn on", async () => {
    const { fetchImpl, calls } = serve(() => status({ suggested_address: "192.168.1.20" }), {
      "POST /api/v1/admin/secure-address/enable": () =>
        jsonResponse(202, status({ state: "registering", lan_address: "192.168.1.20" })),
    });
    renderView(fetchImpl);
    expect(await screen.findByText("The secure address is off.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
    const input = screen.getByLabelText("This computer's address on your home network") as HTMLInputElement;
    expect(input.value).toBe("192.168.1.20");
    expect(screen.getByText("Lumogis found this address. Check that it is right.")).toBeTruthy();
    const turnOn = screen.getByRole("button", { name: "Turn on secure address" }) as HTMLButtonElement;
    expect(turnOn.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Yes, my phone and laptop open Lumogis at this address." }));
    expect(turnOn.disabled).toBe(false);
    fireEvent.click(turnOn);
    expect(await screen.findByText("Getting a name for this Lumogis…")).toBeTruthy();
    expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/enable"))).toMatchObject({
      path: "/api/v1/admin/secure-address/enable",
      body: { lan_address: "192.168.1.20" },
    });
  });

  it("editing the address clears the confirmation", async () => {
    const { fetchImpl } = serve(() => status({ suggested_address: "192.168.1.20" }));
    renderView(fetchImpl);
    const input = await screen.findByLabelText("This computer's address on your home network");
    const box = screen.getByRole("checkbox", { name: "Yes, my phone and laptop open Lumogis at this address." });
    fireEvent.click(box);
    fireEvent.change(input, { target: { value: "10.0.0.7" } });
    expect(isTicked(box)).toBe(false);
  });

  it("off without a suggestion: empty field; a 400 says what a home address is", async () => {
    const { fetchImpl } = serve(() => status(), {
      "POST /api/v1/admin/secure-address/enable": () => jsonResponse(400, { detail: "bad" }),
    });
    renderView(fetchImpl);
    const input = (await screen.findByLabelText(
      "This computer's address on your home network",
    )) as HTMLInputElement;
    expect(input.value).toBe("");
    fireEvent.change(input, { target: { value: "8.8.8.8" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Yes, my phone and laptop open Lumogis at this address." }));
    fireEvent.click(screen.getByRole("button", { name: "Turn on secure address" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      expect.stringMatching(/not a home network address/),
    );
  });

  it("needs_address: explains Docker Desktop and saves with PUT", async () => {
    const { fetchImpl, calls } = serve(
      () => status({ state: "needs_address", detection_unavailable_reason: "docker_desktop" }),
      {
        "PUT /api/v1/admin/secure-address/lan-address": () =>
          jsonResponse(200, status({ state: "registering", lan_address: "192.168.1.30" })),
      },
    );
    renderView(fetchImpl);
    expect(await screen.findByText(/Docker Desktop/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("This computer's address on your home network"), {
      target: { value: "192.168.1.30" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Yes, my phone and laptop open Lumogis at this address." }));
    fireEvent.click(screen.getByRole("button", { name: "Save address" }));
    await screen.findByText("Getting a name for this Lumogis…");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ lan_address: "192.168.1.30" });
  });

  it("ready: shows the address, Copy, certificate date and the source", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { fetchImpl } = serve(() => READY);
    renderView(fetchImpl);
    expect(await screen.findByText("Your secure address is ready.")).toBeTruthy();
    expect(screen.getByTestId("secure-address-url").getAttribute("href")).toBe(URL_);
    expect(screen.getByText(/found automatically/)).toBeTruthy();
    expect(screen.getByText(/Certificate valid until/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy address" }));
    await screen.findByRole("button", { name: "Copied" });
    expect(writeText).toHaveBeenCalledWith(URL_);
  });

  it("Check on this device: opaque answer passes, a TypeError fails", async () => {
    const { fetchImpl } = serve(() => READY);
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Check on this device" }));
    expect(await screen.findByText("This device opens the secure address.")).toBeTruthy();

    const failing = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith(URL_)) throw new TypeError("Failed to fetch");
      return fetchImpl(input, init);
    }) as unknown as typeof fetch;
    globalThis.fetch = failing;
    fireEvent.click(screen.getByRole("button", { name: "Check on this device" }));
    expect(await screen.findByText(/This device cannot open the secure address/)).toBeTruthy();
  });

  it("router_blocked: the router guide is open and names FRITZ!Box and OpenWrt", async () => {
    const { fetchImpl } = serve(() => ({ ...READY, state: "router_blocked", resolver: "blocked" }));
    renderView(fetchImpl);
    expect(await screen.findByText(/your router hides it/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Allow it in your router" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("heading", { name: "FRITZ!Box" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "OpenWrt (dnsmasq)" })).toBeTruthy();
  });

  it("error: says what is wrong, offers Retry and the logs hint", async () => {
    const { fetchImpl, calls } = serve(
      () => status({ state: "error", error_code: "certificate_pending", lan_address: "192.168.1.20" }),
      {
        "POST /api/v1/admin/secure-address/retry": () =>
          jsonResponse(202, status({ state: "issuing", lan_address: "192.168.1.20" })),
      },
    );
    renderView(fetchImpl);
    expect(await screen.findByText(ERROR_TEXT.certificate_pending)).toBeTruthy();
    expect(screen.getByText("docker compose logs caddy")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(/Getting a certificate/);
    expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/retry"))).toBe(true);
  });

  it("Turn off asks first, then posts disable", async () => {
    const { fetchImpl, calls } = serve(() => READY, {
      "POST /api/v1/admin/secure-address/disable": () => jsonResponse(200, status()),
    });
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    expect(calls.some((c) => c.path.endsWith("/disable"))).toBe(false);
    expect(await screen.findByRole("alertdialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Yes, turn it off" }));
    expect(await screen.findByText("The secure address is off.")).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/disable"))).toBe(true);
  });

  it("Turn off: Keep it on closes the confirmation and sends nothing", async () => {
    const { fetchImpl, calls } = serve(() => READY);
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    expect(await screen.findByRole("alertdialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep it on" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(calls.some((c) => c.path.endsWith("/disable"))).toBe(false);
  });

  it("warns when a warning is set or the name changed, and says nothing about sign-in", async () => {
    const { fetchImpl } = serve(() => ({
      ...READY,
      sign_in_enabled: false,
      name_changed: true,
      warnings: ["two_computers"],
    }));
    renderView(fetchImpl);
    expect(await screen.findByText(/Two computers may be using this secure address/)).toBeTruthy();
    expect(screen.queryByText(/Sign-in is off/)).toBeNull();
    expect(screen.getByText(/has a new secure address/)).toBeTruthy();
  });

  it("polls every 3 s while getting a name or certificate, and stops when ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const sequence: SecureAddressStatus[] = [
      status({ state: "registering", lan_address: "192.168.1.20" }),
      status({ state: "issuing", lan_address: "192.168.1.20" }),
      READY,
    ];
    let i = 0;
    const { fetchImpl, calls } = serve(() => sequence[Math.min(i++, sequence.length - 1)]);
    renderView(fetchImpl);
    await screen.findByText("Getting a name for this Lumogis…");
    expect(statusGets(calls)).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2900);
    });
    expect(statusGets(calls)).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    await screen.findByText(/Getting a certificate/);
    expect(statusGets(calls)).toBe(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await screen.findByText("Your secure address is ready.");
    expect(statusGets(calls)).toBe(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    await waitFor(() => expect(statusGets(calls)).toBe(3));
  });
});

describe("AdminSecureAddressView looks for the address (LUM-996)", () => {
  let originalFetch: typeof fetch;
  const DETECT = "POST /api/v1/admin/secure-address/detect";

  beforeEach(() => {
    seedSignedInDevice("admin1");
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("off: one detect after load, a status line while it runs, then the input holds the suggestion", async () => {
    const gate = deferred();
    let current = status();
    const { fetchImpl, calls } = serve(() => current, {
      [DETECT]: async () => {
        await gate.promise;
        current = status({ suggested_address: "192.168.1.30" });
        return jsonResponse(200, current);
      },
    });
    renderView(fetchImpl);
    expect(await screen.findByText(DETECTING_TEXT)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Look again" })).toBeNull();
    expect(addressInput().value).toBe("");
    gate.resolve();
    await waitFor(() => expect(addressInput().value).toBe("192.168.1.30"));
    expect(screen.queryByText(DETECTING_TEXT)).toBeNull();
    expect(detectPosts(calls)).toBe(1);
    // The detect response is not written into the cache: the page read the suggestion by GET.
    expect(statusGets(calls)).toBe(2);
  });

  it("ready: no detect call", async () => {
    const { fetchImpl, calls } = serve(() => READY);
    renderView(fetchImpl);
    await screen.findByText("Your secure address is ready.");
    await settle();
    expect(detectPosts(calls)).toBe(0);
  });

  it("Look again sends a second detect", async () => {
    const { fetchImpl, calls } = serve(() => status(), {
      [DETECT]: () => jsonResponse(200, status()),
    });
    renderView(fetchImpl);
    const again = await screen.findByRole("button", { name: "Look again" });
    expect(detectPosts(calls)).toBe(1);
    fireEvent.click(again);
    await waitFor(() => expect(detectPosts(calls)).toBe(2));
  });

  it("a value the admin typed is not overwritten by a later detect response", async () => {
    const gate = deferred();
    let current = status();
    const { fetchImpl, calls } = serve(() => current, {
      [DETECT]: async () => {
        await gate.promise;
        current = status({ suggested_address: "192.168.1.30" });
        return jsonResponse(200, current);
      },
    });
    renderView(fetchImpl);
    await screen.findByText(DETECTING_TEXT);
    fireEvent.change(addressInput(), { target: { value: "10.0.0.9" } });
    gate.resolve();
    await waitFor(() => expect(statusGets(calls)).toBe(2));
    await settle();
    expect(addressInput().value).toBe("10.0.0.9");
  });

  describe("needs_address with a stored 10.0.0.5, ticked", () => {
    async function setup(afterDetect: Partial<SecureAddressStatus>) {
      const gate = deferred();
      const base = status({ state: "needs_address", lan_address: "10.0.0.5" });
      let current = base;
      const { fetchImpl, calls } = serve(() => current, {
        [DETECT]: async () => {
          await gate.promise;
          current = { ...base, ...afterDetect };
          return jsonResponse(200, current);
        },
      });
      renderView(fetchImpl);
      await screen.findByText(DETECTING_TEXT);
      await waitFor(() => expect(addressInput().value).toBe("10.0.0.5"));
      fireEvent.click(confirmBox());
      expect(isTicked(confirmBox())).toBe(true);
      gate.resolve();
      await waitFor(() => expect(statusGets(calls)).toBe(2));
      await settle();
    }

    it("a different detected address replaces the prefill and unticks the box", async () => {
      await setup({ suggested_address: "10.0.0.7" });
      expect(addressInput().value).toBe("10.0.0.7");
      expect(isTicked(confirmBox())).toBe(false);
    });

    it("the same address leaves the input and the tick alone", async () => {
      await setup({ suggested_address: "10.0.0.5" });
      expect(addressInput().value).toBe("10.0.0.5");
      expect(isTicked(confirmBox())).toBe(true);
    });

    it("a null suggestion leaves the input and the tick alone", async () => {
      await setup({ suggested_address: null });
      expect(addressInput().value).toBe("10.0.0.5");
      expect(isTicked(confirmBox())).toBe(true);
    });
  });

  // VERIFY-PLAN: added — no other test fails if the prefill effect's `suggested === address` guard
  // is removed; this transition (busy flips back after a refused Turn on) is where it matters.
  it("a refused Turn on keeps the input and the tick (the prefill does not re-run on the same address)", async () => {
    const current = status({ suggested_address: "192.168.1.20" });
    const { fetchImpl, calls } = serve(() => current, {
      [DETECT]: async () => jsonResponse(200, current),
      "POST /api/v1/admin/secure-address/enable": async () =>
        jsonResponse(400, { detail: "lan_address_invalid" }),
    });
    renderView(fetchImpl);
    await waitFor(() => expect(addressInput().value).toBe("192.168.1.20"));
    await waitFor(() => expect(statusGets(calls)).toBe(2));
    await settle();
    fireEvent.click(confirmBox());
    fireEvent.click(screen.getByRole("button", { name: "Turn on secure address" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/enable"))).toBe(true));
    await settle();
    expect(addressInput().value).toBe("192.168.1.20");
    expect(isTicked(confirmBox())).toBe(true);
  });

  it("first render pending, then data with a form: no hooks error and one detect", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { fetchImpl, calls } = serve(() => status(), {
      [DETECT]: () => jsonResponse(200, status()),
    });
    renderView(fetchImpl);
    expect(screen.getByText("Loading…")).toBeTruthy();
    await screen.findByText("The secure address is off.");
    await settle();
    expect(detectPosts(calls)).toBe(1);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/hook/i);
  });

  it("Turn on, then Turn off answers off with a new suggestion: the input shows it (typed flag reset)", async () => {
    let current = status();
    const { fetchImpl, calls } = serve(() => current, {
      [DETECT]: () => jsonResponse(200, current),
      "POST /api/v1/admin/secure-address/enable": () => {
        current = status({ state: "registering", lan_address: "10.0.0.9" });
        return jsonResponse(202, current);
      },
      "POST /api/v1/admin/secure-address/disable": () => {
        current = status({ suggested_address: "192.168.1.30" });
        return jsonResponse(200, current);
      },
    });
    renderView(fetchImpl);
    await screen.findByRole("button", { name: "Look again" });
    fireEvent.change(addressInput(), { target: { value: "10.0.0.9" } });
    fireEvent.click(confirmBox());
    fireEvent.click(screen.getByRole("button", { name: "Turn on secure address" }));
    await screen.findByText("Getting a name for this Lumogis…");
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, turn it off" }));
    await screen.findByText("The secure address is off.");
    await waitFor(() => expect(addressInput().value).toBe("192.168.1.30"));
    // The form went away and came back in the same mount: it looked a second time.
    expect(detectPosts(calls)).toBe(2);
  });

  it("a detect response that arrives while Turn on is pending changes neither the input nor the tick", async () => {
    const detectGate = deferred();
    const enableGate = deferred();
    let current = status({ suggested_address: "192.168.1.20" });
    const { fetchImpl, calls } = serve(() => current, {
      [DETECT]: async () => {
        await detectGate.promise;
        current = status({ suggested_address: "192.168.1.99" });
        return jsonResponse(200, current);
      },
      "POST /api/v1/admin/secure-address/enable": async () => {
        await enableGate.promise;
        return jsonResponse(202, status({ state: "registering", lan_address: "192.168.1.20" }));
      },
    });
    renderView(fetchImpl);
    await screen.findByText(DETECTING_TEXT);
    await waitFor(() => expect(addressInput().value).toBe("192.168.1.20"));
    fireEvent.click(confirmBox());
    fireEvent.click(screen.getByRole("button", { name: "Turn on secure address" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/enable"))).toBe(true));

    detectGate.resolve();
    await waitFor(() => expect(statusGets(calls)).toBe(2));
    await settle();
    expect(addressInput().value).toBe("192.168.1.20");
    expect(isTicked(confirmBox())).toBe(true);

    enableGate.resolve();
    await screen.findByText("Getting a name for this Lumogis…");
  });

  it("ready with the name new: refetches after 60 s, and stops once the server returns null", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fresh = { ...READY, name_new_until: "2026-11-18T12:30:00Z" };
    const sequence = [fresh, fresh, READY];
    let i = 0;
    const { fetchImpl, calls } = serve(() => sequence[Math.min(i++, sequence.length - 1)]);
    renderView(fetchImpl);
    await screen.findByText(NAME_NEW_TEXT);
    expect(statusGets(calls)).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(59_000);
    });
    expect(statusGets(calls)).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    await waitFor(() => expect(statusGets(calls)).toBe(2));
    expect(screen.getByText(NAME_NEW_TEXT)).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    await waitFor(() => expect(statusGets(calls)).toBe(3));
    await waitFor(() => expect(screen.queryByText(NAME_NEW_TEXT)).toBeNull());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000);
    });
    expect(statusGets(calls)).toBe(3);
  });

  it("ready, then Turn off answers off: one detect after the form appears", async () => {
    const { fetchImpl, calls } = serve(() => READY, {
      "POST /api/v1/admin/secure-address/disable": () => jsonResponse(200, status()),
      [DETECT]: () => jsonResponse(200, status()),
    });
    renderView(fetchImpl);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    expect(detectPosts(calls)).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Yes, turn it off" }));
    await screen.findByText("The secure address is off.");
    await waitFor(() => expect(detectPosts(calls)).toBe(1));
    await settle();
    expect(detectPosts(calls)).toBe(1);
  });

  it("Turn on stays enabled while a detect is pending, and sends the enable request", async () => {
    const gate = deferred();
    const { fetchImpl, calls } = serve(() => status({ suggested_address: "192.168.1.20" }), {
      [DETECT]: async () => {
        await gate.promise;
        return jsonResponse(200, status());
      },
      "POST /api/v1/admin/secure-address/enable": () =>
        jsonResponse(202, status({ state: "registering", lan_address: "192.168.1.20" })),
    });
    renderView(fetchImpl);
    await screen.findByText(DETECTING_TEXT);
    await waitFor(() => expect(addressInput().value).toBe("192.168.1.20"));
    fireEvent.click(confirmBox());
    const turnOn = screen.getByRole("button", { name: "Turn on secure address" }) as HTMLButtonElement;
    expect(turnOn.disabled).toBe(false);
    fireEvent.click(turnOn);
    await screen.findByText("Getting a name for this Lumogis…");
    expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/enable"))).toBe(true);
    gate.resolve();
  });

  it("a detect that resolves after Turn on does not bring the off form back", async () => {
    const gate = deferred();
    let current = status();
    const { fetchImpl, calls } = serve(() => current, {
      // The stale response says `off`; the server has moved on to `registering`.
      [DETECT]: async () => {
        await gate.promise;
        return jsonResponse(200, status());
      },
      "POST /api/v1/admin/secure-address/enable": () => {
        current = status({ state: "registering", lan_address: "192.168.1.20" });
        return jsonResponse(202, current);
      },
    });
    renderView(fetchImpl);
    await screen.findByText(DETECTING_TEXT);
    fireEvent.change(addressInput(), { target: { value: "192.168.1.20" } });
    fireEvent.click(confirmBox());
    fireEvent.click(screen.getByRole("button", { name: "Turn on secure address" }));
    await screen.findByText("Getting a name for this Lumogis…");

    gate.resolve();
    await waitFor(() => expect(statusGets(calls)).toBe(2));
    await settle();
    expect(screen.getByText("Getting a name for this Lumogis…")).toBeTruthy();
    expect(screen.queryByText("The secure address is off.")).toBeNull();
    expect(screen.queryByLabelText(ADDRESS_LABEL)).toBeNull();
  });

  it("error lan_address_unknown shows the form and detects once", async () => {
    const { fetchImpl, calls } = serve(
      () => status({ state: "error", error_code: "lan_address_unknown" }),
      { [DETECT]: () => jsonResponse(200, status()) },
    );
    renderView(fetchImpl);
    await screen.findByLabelText(ADDRESS_LABEL);
    await waitFor(() => expect(detectPosts(calls)).toBe(1));
    await settle();
    expect(detectPosts(calls)).toBe(1);
  });

  it("a form that changes kind in place (off to needs_address) does not detect again", async () => {
    let current = status();
    const { fetchImpl, calls } = serve(() => current, {
      [DETECT]: () => {
        current = status({ state: "needs_address", detection_unavailable_reason: "failed" });
        return jsonResponse(200, current);
      },
    });
    renderView(fetchImpl);
    await screen.findByText("Lumogis needs this computer's home network address.");
    await settle();
    expect(detectPosts(calls)).toBe(1);
  });

  it("a detect 409 shows no alert and the form stays usable", async () => {
    const { fetchImpl, calls } = serve(() => status(), {
      [DETECT]: () => jsonResponse(409, { detail: "busy" }),
      "POST /api/v1/admin/secure-address/enable": () =>
        jsonResponse(202, status({ state: "registering", lan_address: "192.168.1.20" })),
    });
    renderView(fetchImpl);
    await screen.findByRole("button", { name: "Look again" });
    expect(detectPosts(calls)).toBe(1);
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.change(addressInput(), { target: { value: "192.168.1.20" } });
    fireEvent.click(confirmBox());
    fireEvent.click(screen.getByRole("button", { name: "Turn on secure address" }));
    await screen.findByText("Getting a name for this Lumogis…");
  });

  it("a failing device check says the name is new while the window is open, and blames the router otherwise", async () => {
    const failing = (fetchImpl: typeof fetch) =>
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).startsWith(URL_)) throw new TypeError("Failed to fetch");
        return fetchImpl(input, init);
      }) as unknown as typeof fetch;

    const fresh = serve(() => ({ ...READY, name_new_until: "2026-11-18T12:30:00Z" }));
    renderView(fresh.fetchImpl);
    await screen.findByText("Your secure address is ready.");
    globalThis.fetch = failing(fresh.fetchImpl);
    fireEvent.click(screen.getByRole("button", { name: "Check on this device" }));
    expect(await screen.findByText(NAME_NEW_CHECK_FAILED)).toBeTruthy();
    expect(screen.queryByText(/your router probably hides it/)).toBeNull();
  });

  it("a failing device check with no new-name window keeps the router text", async () => {
    const { fetchImpl } = serve(() => READY);
    renderView(fetchImpl);
    await screen.findByText("Your secure address is ready.");
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith(URL_)) throw new TypeError("Failed to fetch");
      return fetchImpl(input, init);
    }) as unknown as typeof fetch;
    fireEvent.click(screen.getByRole("button", { name: "Check on this device" }));
    expect(await screen.findByText(/your router probably hides it/)).toBeTruthy();
    expect(screen.queryByText(NAME_NEW_CHECK_FAILED)).toBeNull();
  });
});
