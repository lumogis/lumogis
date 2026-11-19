// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Read-only LLM providers view — GET /api/v1/me/llm-providers only.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeLlmProvidersView } from "../../../src/features/me/MeLlmProvidersView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

describe("MeLlmProvidersView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    seedSignedInDevice("u1");
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const user = { id: "u1", email: "u@home.lan", role: "user" as const };

  const samplePayload = {
    providers: [
      {
        connector: "llm_openai",
        label: "OpenAI",
        description: "OpenAI API key. Per-user; credential.",
        configured: true,
        active_tier: "user",
        user_credential_present: true,
        household_credential_available: false,
        system_credential_available: false,
        env_fallback_available: false,
        updated_at: "2026-04-25T12:00:00Z",
        key_version: 2,
        status: "configured",
        why_not_available: null,
      },
      {
        connector: "llm_anthropic",
        label: "Anthropic",
        description: "Anthropic.",
        configured: false,
        active_tier: "none",
        user_credential_present: false,
        household_credential_available: false,
        system_credential_available: false,
        env_fallback_available: false,
        updated_at: null,
        key_version: null,
        status: "not_configured",
        why_not_available: "No credential stored at user, household, or system tier, and no env fallback.",
      },
    ],
    summary: {
      total: 2,
      configured: 1,
      not_configured: 1,
      by_active_tier: { user: 1, none: 1 },
    },
  };

  it("calls GET /api/v1/me/llm-providers and renders summary", async () => {
    let url: string | null = null;
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/llm-providers") && (_init?.method === "GET" || _init?.method === undefined)) {
        url = u;
        return jsonResponse(200, samplePayload);
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeLlmProvidersView />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(url).toContain("/api/v1/me/llm-providers"));
    await waitFor(() => expect(screen.getByTestId("llm-summary")).toHaveTextContent("1 of 2 connected."));
  });

  it("renders configured and not configured rows without secret fields", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/llm-providers")) return jsonResponse(200, samplePayload);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeLlmProvidersView />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("OpenAI")).toBeInTheDocument());
    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    // Plain words in the main view: a Badge for state, no provider ids.
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.queryByText("llm_openai")).not.toBeInTheDocument();
    expect(screen.queryByText("llm_anthropic")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /save|delete|reveal/i })).toBeNull();
    expect(screen.queryByText(/sk-/)).toBeNull();
  });

  async function mountSample() {
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/llm-providers")) return jsonResponse(200, samplePayload);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeLlmProvidersView />
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByText("OpenAI");
  }

  it("says where a connected key comes from, in words", async () => {
    await mountSample();
    expect(screen.getByText("Key from: Your account")).toBeInTheDocument();
    expect(screen.queryByText("Key from: user")).not.toBeInTheDocument();
  });

  it("keeps the provider id, dates and key version under Details", async () => {
    await mountSample();
    const u = userEvent.setup();
    expect(screen.queryByText("llm_openai")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Details for OpenAI" }));
    expect(await screen.findByText("llm_openai")).toBeInTheDocument();
    expect(screen.getByText(/25 Apr 2026/)).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("You have saved your own key.")).toBeInTheDocument();
  });

  it("filters to connected providers only", async () => {
    await mountSample();
    const u = userEvent.setup();
    await u.click(screen.getByRole("combobox", { name: "Filter AI services" }));
    await u.click(await screen.findByRole("option", { name: "Connected only" }));
    await waitFor(() => expect(screen.queryByText("Anthropic")).not.toBeInTheDocument());
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
  });

  it("says so when a filter matches nothing", async () => {
    await mountSample();
    const u = userEvent.setup();
    await u.click(screen.getByRole("combobox", { name: "Filter AI services" }));
    await u.click(await screen.findByRole("option", { name: "Key shared by the household" }));
    expect(await screen.findByText("No AI services match this filter.")).toBeInTheDocument();
  });

  it("points to Connected services to change keys, with a styled link", async () => {
    await mountSample();
    const link = within(screen.getByRole("heading", { name: "Your AI services" }).closest('[data-slot="card"]') as HTMLElement).getByRole("link", { name: "Connected services" });
    expect(link).toHaveAttribute("href", "/settings/connections");
  });

  it("renders error state on API failure", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/llm-providers")) return jsonResponse(500, { detail: "server boom" });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeLlmProvidersView />
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
