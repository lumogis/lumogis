// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — MeConnectorsView: registry failure shows hint banner; list still drives rows.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeConnectorsView } from "../../../src/features/me/MeConnectorsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

describe("MeConnectorsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows fallback banner when the registry call fails but credentials list loads", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const listItems = { items: [{ connector: "cal_dav_1", updated_at: "2020-01-01" }] };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/registry")) return new Response("bad", { status: 500 });
      if (u.includes("/connector-credentials") && !u.includes("registry")) return jsonResponse(200, listItems);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      // LUM-834 chunk B — the view now renders a router `Link` on its own
      // surface (the Email-signals signpost that replaced the imap/smtp
      // cards), so it needs a Router even on the registry-failure path.
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeConnectorsView />
        </AuthProvider>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(
        screen.getByText(/Connector schema hints unavailable; using JSON fallback for listed connectors\./i),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("cal_dav_1")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// LUM-834 chunk A (G1) — Test connection.
// ---------------------------------------------------------------------------

describe("MeConnectorsView — test connection", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const authUser = { id: "u1", email: "u@home.lan", role: "user" as const };

  function mount(
    registry: { id: string; description: string }[],
    onTest: (connector: string) => Response,
    basePath?: string,
  ) {
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, authUser);
      if (u.includes("/registry")) return jsonResponse(200, { items: registry });
      if (u.endsWith("/test") && init?.method === "POST") {
        const connector = u.split("/").slice(-2)[0];
        return onTest(connector);
      }
      if (u.includes("/connector-credentials")) return jsonResponse(200, { items: [] });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeConnectorsView basePath={basePath} />
        </AuthProvider>
      </MemoryRouter>,
    );
    return fetchImpl;
  }

  it("renders a ✓ for a verified connector", async () => {
    mount([{ id: "paperless", description: "" }], () =>
      jsonResponse(200, { ok: true, code: "verified", detail: "Connected successfully." }),
    );
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: /test connection/i }));
    await waitFor(() => {
      expect(screen.getByText(/Connected successfully\./)).toBeInTheDocument();
    });
    expect(screen.getByText("✓", { exact: false })).toBeInTheDocument();
  });

  it("ok=null renders NEITHER a ✓ nor a ✗", async () => {
    // A ✗ on a connector that simply has no probe is a red mark on something
    // that is working perfectly — the defect this whole tri-state exists for.
    mount([{ id: "ntfy", description: "" }], () =>
      jsonResponse(200, {
        ok: null,
        code: "unsupported",
        detail: "No connection test for this connector.",
      }),
    );
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: /test connection/i }));
    const chip = await screen.findByText(/No connection test for this connector\./);
    expect(chip.textContent).not.toMatch(/[✓✗]/);
  });

  it("a failed test renders a ✗ and the server's copy", async () => {
    mount([{ id: "paperless", description: "" }], () =>
      jsonResponse(200, {
        ok: false,
        code: "auth_failed",
        detail: "The server rejected these credentials.",
      }),
    );
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: /test connection/i }));
    const chip = await screen.findByText(/The server rejected these credentials\./);
    expect(chip.textContent).toMatch(/✗/);
  });

  it.each(["imap", "smtp"])(
    "%s shows a link to the mailbox surface, not a dead Test button",
    async (connector) => {
      // Both are multi-instance and this path has nowhere to put a mailbox id,
      // so the backend answers `tested_elsewhere` for both. A button whose only
      // possible answer is "test this somewhere else" wastes a click to deliver
      // a link — so render the link. `smtp` was the case a frontend
      // special-case for `imap` alone silently got wrong.
      mount([{ id: connector, description: "" }], () => jsonResponse(500, {}));
      expect(await screen.findByRole("link", { name: /email signals/i })).toHaveAttribute(
        "href",
        "/me/email-signals",
      );
      expect(screen.queryByRole("button", { name: /test connection/i })).not.toBeInTheDocument();
    },
  );

  it("a 429 says to wait, rather than 'try again'", async () => {
    mount([{ id: "paperless", description: "" }], () =>
      jsonResponse(429, { detail: { code: "rate_limited" } }),
    );
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: /test connection/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/wait a minute/i);
  });

  it("renders NO Test affordance on an admin tier basePath", async () => {
    // `probe(connector, user_id)` has no tier axis, so a Test button here would
    // silently test the ADMIN'S OWN credential while presenting as a household
    // or system test. The admin mirror is deferred until the signature carries
    // a tier — this pins that it is genuinely absent rather than merely
    // unmentioned.
    mount(
      [
        { id: "paperless", description: "" },
        { id: "imap", description: "" },
      ],
      () => jsonResponse(500, {}),
      "/api/v1/admin/connector-credentials/household",
    );
    await screen.findByText("paperless");
    expect(screen.queryByRole("button", { name: /test connection/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /email signals/i })).not.toBeInTheDocument();
  });
});
