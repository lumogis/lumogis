// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-65 — Admin → System mail: enablement diagnosis, keep-password semantics,
// and the Send test that is the only thing which turns email reset on.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSystemMailView } from "../../../src/features/admin/AdminSystemMailView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("admin");
});

interface SystemMailShape {
  configured: boolean;
  host: string | null;
  port: number | null;
  username: string | null;
  from_addr: string | null;
  password_set: boolean;
  last_test_ok_at: string | null;
  email_reset_enabled: boolean;
  public_origin_set: boolean;
  altcha_hmac_set: boolean;
}

function systemMail(over: Partial<SystemMailShape> = {}): SystemMailShape {
  return {
    configured: true,
    host: "smtp.example.com",
    port: 587,
    username: "lumogis@example.com",
    from_addr: "lumogis@example.com",
    password_set: true,
    last_test_ok_at: "2026-08-08T10:00:00Z",
    email_reset_enabled: true,
    public_origin_set: true,
    altcha_hmac_set: true,
    ...over,
  };
}

function setup(current: SystemMailShape, testResponse?: Response) {
  const user = { id: "admin", email: "admin@home.lan", role: "admin" as const };
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
    if (url.includes("/api/v1/admin/system-mail/test") && method === "POST") {
      return testResponse ?? jsonResponse(200, { ok: true, last_test_ok_at: "2026-08-08T11:00:00Z" });
    }
    if (url.includes("/api/v1/admin/system-mail")) {
      if (method === "PUT") return jsonResponse(200, systemMail());
      if (method === "DELETE") return jsonResponse(200, { ok: true });
      return jsonResponse(200, current);
    }
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, fetchImpl };
}

function renderView(client: ApiClient, tokens: AccessTokenStore) {
  return render(
    <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
      <AdminSystemMailView />
    </AuthProvider>,
  );
}

describe("AdminSystemMailView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("names the missing precondition when email reset is not enabled", async () => {
    // Enablement is a conjunction; an operator needs to know which conjunct
    // failed, and the public status endpoint deliberately will not say.
    const { client, tokens } = setup(
      systemMail({ email_reset_enabled: false, public_origin_set: false }),
    );

    renderView(client, tokens);

    const summary = await screen.findByTestId("system-mail-enablement");
    expect(summary.textContent).toMatch(/not enabled/i);
    expect(summary.textContent).toMatch(/Public address set/);
    // The env var name is developer detail: collapsed Details only, not the checklist.
    expect(summary.textContent).not.toMatch(/LUMOGIS_PUBLIC_ORIGIN/);
  });

  it("says a Send test is still needed when the relay has never delivered", async () => {
    const { client, tokens } = setup(
      systemMail({ email_reset_enabled: false, last_test_ok_at: null }),
    );

    renderView(client, tokens);

    const summary = await screen.findByTestId("system-mail-enablement");
    expect(summary.textContent).toMatch(/Send test/);
    expect(summary.textContent).toMatch(/Test message delivered/);
  });

  it("never renders the stored password and omits it when left blank", async () => {
    const { client, tokens, fetchImpl } = setup(systemMail());
    const user = userEvent.setup();

    renderView(client, tokens);
    await screen.findByTestId("system-mail-save");

    const password = screen.getByLabelText(/^password$/i) as HTMLInputElement;
    expect(password.value).toBe("");
    expect(password.placeholder).toMatch(/leave blank to keep/i);

    await user.click(screen.getByTestId("system-mail-save"));

    await waitFor(() => {
      const put = fetchImpl.mock.calls.find(
        (c) => (c[1] as RequestInit)?.method === "PUT",
      );
      expect(put).toBeTruthy();
      const body = JSON.parse(String((put![1] as RequestInit).body));
      // Absent, not "" — an explicit empty string is a 422 server-side.
      expect("password" in body).toBe(false);
      expect(body.host).toBe("smtp.example.com");
    });
  });

  it("sends the password only when the admin typed a new one", async () => {
    const { client, tokens, fetchImpl } = setup(systemMail());
    const user = userEvent.setup();

    renderView(client, tokens);
    await screen.findByTestId("system-mail-save");
    await user.type(screen.getByLabelText(/^password$/i), "new-relay-secret");
    await user.click(screen.getByTestId("system-mail-save"));

    await waitFor(() => {
      const put = fetchImpl.mock.calls.find(
        (c) => (c[1] as RequestInit)?.method === "PUT",
      );
      const body = JSON.parse(String((put![1] as RequestInit).body));
      expect(body.password).toBe("new-relay-secret");
    });
  });

  it("refetches after a successful Send test so enablement updates", async () => {
    const { client, tokens, fetchImpl } = setup(
      systemMail({ email_reset_enabled: false, last_test_ok_at: null }),
    );
    const user = userEvent.setup();

    renderView(client, tokens);
    await screen.findByTestId("system-mail-test");
    await user.click(screen.getByTestId("system-mail-test"));

    await waitFor(() => {
      const gets = fetchImpl.mock.calls.filter(
        (c) =>
          String(c[0]).includes("/api/v1/admin/system-mail") &&
          !String(c[0]).includes("/test") &&
          ((c[1] as RequestInit)?.method ?? "GET") === "GET",
      );
      expect(gets.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("surfaces a relay failure instead of claiming success", async () => {
    const { client, tokens } = setup(
      systemMail({ email_reset_enabled: false, last_test_ok_at: null }),
      jsonResponse(502, { detail: { code: "send_failed", message: "smtp_auth: rejected" } }),
    );
    const user = userEvent.setup();

    renderView(client, tokens);
    await screen.findByTestId("system-mail-test");
    await user.click(screen.getByTestId("system-mail-test"));

    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("asks before removing the email settings, and Cancel does nothing", async () => {
    const { client, tokens, fetchImpl } = setup(systemMail());
    const user = userEvent.setup();

    renderView(client, tokens);
    await user.click(await screen.findByTestId("system-mail-delete"));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(fetchImpl.mock.calls.some((c) => (c[1] as RequestInit)?.method === "DELETE")).toBe(false);
  });

  it("removes the email settings once confirmed", async () => {
    const { client, tokens, fetchImpl } = setup(systemMail());
    const user = userEvent.setup();

    renderView(client, tokens);
    await user.click(await screen.findByTestId("system-mail-delete"));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some((c) => (c[1] as RequestInit)?.method === "DELETE")).toBe(true),
    );
  });

  it("shows a retry when the settings cannot load", async () => {
    const { client, tokens, fetchImpl } = setup(systemMail());
    const base = fetchImpl.getMockImplementation()!;
    fetchImpl.mockImplementation(async (input: RequestInfo, init?: RequestInit) =>
      String(input).includes("/api/v1/admin/system-mail") && !String(input).includes("/test")
        ? jsonResponse(500, { detail: "boom" })
        : base(input, init),
    );

    renderView(client, tokens);
    expect(await screen.findByRole("button", { name: /try again/i })).toBeTruthy();
  });

  it("disables Send test until SMTP is configured", async () => {
    const { client, tokens } = setup(
      systemMail({
        configured: false,
        host: null,
        port: null,
        username: null,
        from_addr: null,
        password_set: false,
        last_test_ok_at: null,
        email_reset_enabled: false,
      }),
    );

    renderView(client, tokens);

    const button = await screen.findByTestId("system-mail-test");
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});
