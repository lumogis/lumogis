// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AuditLogView } from "../../../src/features/audit/AuditLogView";
import { jsonResponse } from "../../helpers/jsonResponse";

function hangingSseResponse(): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode('id: 2\nevent: audit_entry\ndata: {"id":2,"action_name":"live"}\n\n'),
      );
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function auditListResponse(eventType = "privacy.external_call.denied") {
  return jsonResponse(200, {
    audit: [
      {
        id: 1,
        action_name: "privacy_mode_block",
        connector: "llm",
        mode: "privacy_gate",
        input_summary: '{"decline_type":"external_call_denied"}',
        result_summary: "",
        reverse_token: null,
        reverse_action: null,
        executed_at: "2026-04-24T00:00:00Z",
        reversed_at: null,
        event_type: eventType,
        scope: "personal",
        source: "llm/privacy_gate",
        description: "decline_type=external_call_denied",
      },
    ],
    total: 1,
    limit: 50,
    offset: 0,
  });
}

function setup() {
  const user = { id: "member", email: "member@home.lan", role: "user" as const };
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
    if (url.includes("/api/v1/audit") && (!init?.method || init.method === "GET")) return auditListResponse();
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, fetchImpl };
}

describe("AuditLogView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders audit rows without reverse control", async () => {
    const { client, tokens } = setup();
    render(
      <MemoryRouter initialEntries={["/audit"]}>
        <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
          <Routes>
            <Route path="/audit" element={<AuditLogView />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: /audit log/i });
    expect(screen.queryByRole("button", { name: /^reverse$/i })).toBeNull();
    expect(await screen.findByText(/decline_type=external_call_denied/i)).toBeInTheDocument();
  });

  it("shows privacy badge for privacy event types", async () => {
    const { client, tokens } = setup();
    render(
      <MemoryRouter initialEntries={["/audit"]}>
        <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
          <Routes>
            <Route path="/audit" element={<AuditLogView />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId("privacy-badge");
  });

  it("filter chip updates query string", async () => {
    const { client, tokens, fetchImpl } = setup();
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/audit"]}>
        <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
          <Routes>
            <Route path="/audit" element={<AuditLogView />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: /audit log/i });
    await user.click(screen.getByRole("button", { name: /^privacy$/i }));
    await waitFor(() => {
      const hit = fetchImpl.mock.calls.some((c) =>
        String(c[0]).includes("event_type=privacy.external_call.denied"),
      );
      expect(hit).toBe(true);
    });
  });

  it("expand toggles detail with raw event_type", async () => {
    const { client, tokens } = setup();
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/audit"]}>
        <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
          <Routes>
            <Route path="/audit" element={<AuditLogView />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("button", { name: /^details$/i }));
    expect(screen.getByText("privacy.external_call.denied")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^hide$/i }));
    expect(screen.queryByText("privacy.external_call.denied")).toBeNull();
  });

  it("live toggle opens audit stream", async () => {
    const { client, tokens, fetchImpl } = setup();
    fetchImpl.mockImplementation(async (input: RequestInfo, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return jsonResponse(200, { id: "member", email: "m@h", role: "user" });
      if (url.includes("/api/v1/audit/stream")) return hangingSseResponse();
      if (url.includes("/api/v1/audit")) return auditListResponse();
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl as unknown as typeof fetch;
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/audit"]}>
        <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
          <Routes>
            <Route path="/audit" element={<AuditLogView />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: /audit log/i });
    await user.click(screen.getByRole("button", { name: /live \(off\)/i }));
    await waitFor(() => {
      expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("/api/v1/audit/stream"))).toBe(true);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/live tail on/i);
  });
});
