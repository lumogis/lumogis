// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Read-only LLM providers view — GET /api/v1/me/llm-providers only.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeLlmProvidersView } from "../../../src/features/me/MeLlmProvidersView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MeLlmProvidersView", () => {
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
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeLlmProvidersView />
      </AuthProvider>,
    );

    await waitFor(() => expect(url).toContain("/api/v1/me/llm-providers"));
    await waitFor(() => expect(screen.getByLabelText(/Total providers: 2/)).toBeInTheDocument());
    expect(screen.getByLabelText(/Configured providers: 1/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Not configured: 1/)).toBeInTheDocument();
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
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeLlmProvidersView />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByText("OpenAI")).toBeInTheDocument());
    expect(screen.getByText("llm_openai")).toBeInTheDocument();
    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /save|delete|reveal/i })).toBeNull();
    expect(screen.queryByText(/sk-/)).toBeNull();
  });

  it("uses bounded table scroll wrapper for the providers table (Phase 2C)", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/llm-providers")) return jsonResponse(200, samplePayload);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeLlmProvidersView />
      </AuthProvider>,
    );

    const table = await screen.findByRole("table");
    expect(table.closest(".lumogis-table-scroll")).toBeTruthy();
    expect(table).toHaveClass("lumogis-dense-table");
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
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeLlmProvidersView />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
