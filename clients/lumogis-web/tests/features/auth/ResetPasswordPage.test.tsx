// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-65 — redeem page: token handling, confirmation match, history hygiene.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ResetPasswordPage } from "../../../src/features/auth/ResetPasswordPage";

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return { ...actual, useNavigate: () => navigateSpy };
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ResetPasswordPage />
    </MemoryRouter>,
  );
}

describe("ResetPasswordPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an invalid-link message when the URL carries no token", () => {
    renderAt("/reset-password");

    expect(screen.getByTestId("reset-password-no-token")).toBeTruthy();
  });

  it("strips the token from the URL so it does not sit in history", async () => {
    const replaceState = vi.spyOn(window.history, "replaceState");

    renderAt("/reset-password?token=prst_abc");

    await waitFor(() => {
      expect(replaceState).toHaveBeenCalledWith({}, "", "/reset-password");
    });
  });

  it("refuses to submit when the two passwords differ", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderAt("/reset-password?token=prst_abc");

    await userEvent.type(screen.getByLabelText(/^new password$/i), "a-long-password-1");
    await userEvent.type(screen.getByLabelText(/confirm/i), "a-different-password");
    await userEvent.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("redeems and sends the user back to sign in", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ ok: true })));
    renderAt("/reset-password?token=prst_abc");

    await userEvent.type(screen.getByLabelText(/^new password$/i), "a-long-password-1");
    await userEvent.type(screen.getByLabelText(/confirm/i), "a-long-password-1");
    await userEvent.click(screen.getByRole("button", { name: /set new password/i }));

    await waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith("/");
    });
    // Every session for the account is dead server-side, so the only honest
    // next step is a fresh sign-in — with a flash saying why.
    expect(sessionStorage.getItem("lumogis_login_flash")).toMatch(/password updated/i);
  });

  it("surfaces an expired or already-used link", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "Reset link is invalid or expired" }, 404)),
    );
    renderAt("/reset-password?token=prst_abc");

    await userEvent.type(screen.getByLabelText(/^new password$/i), "a-long-password-1");
    await userEvent.type(screen.getByLabelText(/confirm/i), "a-long-password-1");
    await userEvent.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Reset link is invalid or expired",
    );
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});
