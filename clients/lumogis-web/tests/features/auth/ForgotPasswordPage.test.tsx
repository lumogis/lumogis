// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-65 — the forgot-password page adapts to `email_reset_enabled`, and never
// tells the caller whether an address has an account.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ForgotPasswordPage } from "../../../src/features/auth/ForgotPasswordPage";

vi.mock("../../../src/features/auth/altcha", () => ({
  // The proof of work itself is exercised for real in the orchestrator suite
  // (tests/test_altcha_challenge.py) and end-to-end in Playwright; here it
  // would just make every assertion wait on a KDF.
  solveAltchaChallenge: vi.fn(async () => "solved-payload"),
}));

function mockFetch(handlers: Record<string, () => Response>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      for (const [prefix, make] of Object.entries(handlers)) {
        if (url.startsWith(prefix)) return make();
      }
      return new Response("{}", { status: 404 });
    }),
  );
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/forgot-password"]}>
      <ForgotPasswordPage />
    </MemoryRouter>,
  );
}

describe("ForgotPasswordPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows ask-an-admin and CLI guidance when email reset is disabled", async () => {
    mockFetch({
      "/api/v1/password-reset/status": () => json({ email_reset_enabled: false }),
    });

    renderPage();

    await screen.findByTestId("forgot-password-disabled");
    expect(screen.getByText(/ask an admin/i)).toBeTruthy();
    expect(screen.getByText(/python -m scripts.reset_password/)).toBeTruthy();
    expect(screen.queryByTestId("forgot-password-form")).toBeNull();
  });

  it("falls back to the disabled guidance when status cannot be reached", async () => {
    // Fail closed: showing a form that cannot work is worse than showing the
    // path that always works.
    mockFetch({ "/api/v1/password-reset/status": () => json({}, 500) });

    renderPage();

    await screen.findByTestId("forgot-password-disabled");
  });

  it("shows the email form when email reset is enabled", async () => {
    mockFetch({
      "/api/v1/password-reset/status": () => json({ email_reset_enabled: true }),
    });

    renderPage();

    await screen.findByTestId("forgot-password-form");
    expect(screen.getByLabelText(/email/i)).toBeTruthy();
  });

  it("shows the same neutral confirmation regardless of whether the account exists", async () => {
    mockFetch({
      "/api/v1/password-reset/status": () => json({ email_reset_enabled: true }),
      "/api/v1/altcha/challenge": () => json({ parameters: {}, signature: "sig" }),
      "/api/v1/password-reset/request": () => json({ ok: true }),
    });

    renderPage();
    await screen.findByTestId("forgot-password-form");
    await userEvent.type(screen.getByLabelText(/email/i), "someone@example.com");
    await userEvent.click(screen.getByRole("button", { name: /send reset link/i }));

    const sent = await screen.findByTestId("forgot-password-sent");
    // "If that address has an account" — never a confirmation either way.
    expect(sent.textContent).toMatch(/if that address has a lumogis account/i);
  });

  it("drops back to the guidance when the server reports the feature is off", async () => {
    mockFetch({
      "/api/v1/password-reset/status": () => json({ email_reset_enabled: true }),
      "/api/v1/altcha/challenge": () => json({ parameters: {}, signature: "sig" }),
      "/api/v1/password-reset/request": () =>
        json({ detail: { code: "email_reset_disabled" } }, 503),
    });

    renderPage();
    await screen.findByTestId("forgot-password-form");
    await userEvent.type(screen.getByLabelText(/email/i), "someone@example.com");
    await userEvent.click(screen.getByRole("button", { name: /send reset link/i }));

    await waitFor(() => {
      expect(screen.getByTestId("forgot-password-disabled")).toBeTruthy();
    });
  });
});
