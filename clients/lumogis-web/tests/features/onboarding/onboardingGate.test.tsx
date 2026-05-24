// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { ModalFrame } from "../../../src/features/onboarding/modalFrame";
import { OnboardingGate } from "../../../src/features/onboarding/OnboardingGate";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ModalFrame", () => {
  it("Escape invokes onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ModalFrame open titleId="t1" onClose={onClose}>
        <h2 id="t1">Title</h2>
        <button type="button">Inside</button>
      </ModalFrame>,
    );
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Tab from last focusable wraps focus to first", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ModalFrame open titleId="t1" onClose={onClose}>
        <h2 id="t1">Title</h2>
        <button type="button">First</button>
        <button type="button">Last</button>
      </ModalFrame>,
    );
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });

    await waitFor(() => {
      expect(first).toHaveFocus();
    });

    await user.tab();
    expect(last).toHaveFocus();

    await user.tab();
    expect(first).toHaveFocus();
  });

  it("Shift+Tab from first focusable wraps focus to last", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ModalFrame open titleId="t1" onClose={onClose}>
        <h2 id="t1">Title</h2>
        <button type="button">First</button>
        <button type="button">Last</button>
      </ModalFrame>,
    );
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });

    await waitFor(() => {
      expect(first).toHaveFocus();
    });

    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(last).toHaveFocus();
  });
});

describe("OnboardingGate", () => {
  it("does not render modal while onboarding query is pending", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let resolveOnb!: (v: Response) => void;
    const onbPromise = new Promise<Response>((resolve) => {
      resolveOnb = resolve;
    });
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/auth/me")) {
        return jsonResponse(200, { id: "u1", email: "a@a", role: "user" });
      }
      if (url.endsWith("/api/v1/me/onboarding")) {
        return onbPromise;
      }
      throw new Error(`unexpected: ${url}`);
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <MemoryRouter>
          <OnboardingGate />
        </MemoryRouter>
      </AuthProvider>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    resolveOnb!(jsonResponse(200, { completed_at: null }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("shows retry banner when GET onboarding fails", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/auth/me")) {
        return jsonResponse(200, { id: "u1", email: "a@a", role: "user" });
      }
      if (url.endsWith("/api/v1/me/onboarding")) {
        return jsonResponse(503, { detail: "database_unavailable" });
      }
      throw new Error(`unexpected: ${url}`);
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <MemoryRouter>
          <OnboardingGate />
        </MemoryRouter>
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
