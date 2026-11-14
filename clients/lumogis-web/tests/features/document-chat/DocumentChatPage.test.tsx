// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest — document chat streaming (LUM-876).
//
// Document chat renders the assistant text as plain text, so a status that
// arrives as answer content shows its markdown and stays in the answer. The
// cold-local-model hint now arrives as `lumogis.status`; this pins that the
// page shows it while waiting and drops it when the first token arrives.

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { DocumentChatPage } from "../../../src/features/document-chat/DocumentChatPage";
import { renderWithRouter } from "../../helpers/renderWithRouter";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function chunk(delta: object, extra: object = {}, finish: "stop" | null = null): string {
  const payload = {
    id: "chatcmpl-lumogis",
    object: "chat.completion.chunk",
    created: 1_700_000_000,
    model: "llama",
    choices: [{ index: 0, delta, finish_reason: finish }],
    ...extra,
  };
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function controllableBody(): {
  body: ReadableStream<Uint8Array>;
  push(part: string): void;
  close(): void;
} {
  const encoder = new TextEncoder();
  let ctrl: ReadableStreamDefaultController<Uint8Array> | null = null;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c;
    },
  });
  return {
    body,
    push: (part) => ctrl?.enqueue(encoder.encode(part)),
    close: () => ctrl?.close(),
  };
}

const LLAMA = { id: "llama", label: "Llama", is_local: true, enabled: true, provider: "ollama" };

interface BuildOpts {
  models?: Record<string, unknown>[];
  /** Records the JSON body of every `POST /api/v1/chat/completions`. */
  chatBodies?: Record<string, unknown>[];
  /** Counts `GET /api/v1/me/privacy-mode` requests. */
  privacyCalls?: { n: number };
}

function buildClient(chatBody: ReadableStream<Uint8Array>, opts: BuildOpts = {}): ApiClient {
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/auth/refresh"))
      return jsonResponse(200, {
        access_token: "ax-1",
        token_type: "bearer",
        expires_in: 900,
        user: { id: "u1", email: "alice@home.lan", role: "user" },
      });
    if (url.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" });
    if (url.endsWith("/api/v1/models")) return jsonResponse(200, { models: opts.models ?? [LLAMA] });
    if (url.includes("/api/v1/me/privacy-mode")) {
      if (opts.privacyCalls) opts.privacyCalls.n += 1;
      return jsonResponse(200, {
        instance: { privacy_mode: "local_only", privacy_mode_locked: false, privacy_effective: "local_only" },
        user_restriction: "inherit",
        privacy_effective: "local_only",
        can_allow_cloud: false,
      });
    }
    if (url.endsWith("/api/v1/documents/7"))
      return jsonResponse(200, { title: "notes.md", file_path: "/docs/notes.md" });
    if (url.endsWith("/api/v1/chat/completions")) {
      opts.chatBodies?.push(JSON.parse(String(init?.body ?? "{}")));
      return new Response(chatBody, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
  return new ApiClient({ tokens: new AccessTokenStore(), fetchImpl: fetchImpl as unknown as typeof fetch });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DocumentChatPage — stream status (LUM-876)", () => {
  it("shows the loading status while waiting, then answers without it", async () => {
    const user = userEvent.setup();
    const stream = controllableBody();
    const client = buildClient(stream.body);
    renderWithRouter(
      <AuthProvider client={client} tokens={new AccessTokenStore()}>
        <Routes>
          <Route path="/documents/:documentId/chat" element={<DocumentChatPage />} />
        </Routes>
      </AuthProvider>,
      { route: "/documents/7/chat" },
    );

    const composer = await screen.findByPlaceholderText(/ask about this document/i);
    await user.type(composer, "What is in it?");
    await user.keyboard("{Enter}");

    act(() => {
      stream.push(chunk({ role: "assistant", content: "" }));
      stream.push(
        chunk(
          {},
          { lumogis: { status: { code: "local_model_loading", message: "Loading model on your machine…" } } },
        ),
      );
    });
    const status = await screen.findByTestId("document-chat-stream-status");
    expect(status).toHaveTextContent("Loading model on your machine…");
    expect(status).not.toHaveTextContent("*");

    act(() => {
      stream.push(chunk({ content: "It lists groceries." }, {}, "stop"));
      stream.push("data: [DONE]\n\n");
      stream.close();
    });
    await waitFor(() => {
      expect(screen.getByText("It lists groceries.")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("document-chat-stream-status")).not.toBeInTheDocument();
    expect(screen.getByTestId("document-chat-page")).not.toHaveTextContent(/Loading model/);
  });
});

function mountPage(client: ApiClient) {
  return renderWithRouter(
    <AuthProvider client={client} tokens={new AccessTokenStore()}>
      <Routes>
        <Route path="/documents/:documentId/chat" element={<DocumentChatPage />} />
      </Routes>
    </AuthProvider>,
    { route: "/documents/7/chat" },
  );
}

const CLAUDE_BLOCKED = {
  id: "claude",
  label: "Claude",
  is_local: false,
  enabled: false,
  provider: "anthropic",
  disabled_reason: "privacy_mode",
};

describe("DocumentChatPage — privacy-mode hint (LUM-881)", () => {
  it("(a) one enabled local model + one privacy_mode cloud model: hint shown, picker hidden", async () => {
    const privacyCalls = { n: 0 };
    mountPage(buildClient(controllableBody().body, { models: [LLAMA, CLAUDE_BLOCKED], privacyCalls }));
    const hint = await screen.findByTestId("privacy-blocked-hint");
    expect(hint).toHaveTextContent("Cloud models are off");
    // Only one model is enabled, so the picker itself is not rendered.
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("(b) every model enabled: no hint, and no policy request", async () => {
    const privacyCalls = { n: 0 };
    mountPage(buildClient(controllableBody().body, { privacyCalls }));
    await screen.findByPlaceholderText(/ask about this document/i);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument();
    expect(privacyCalls.n).toBe(0);
  });

  it("(d) the first enabled model in server order is the one used, even if claude is also enabled", async () => {
    // Today's rule: models[0] replaces DEFAULT_MODEL unconditionally. A
    // "keep the current model if it is enabled" rule would send `claude`.
    const user = userEvent.setup();
    const chatBodies: Record<string, unknown>[] = [];
    const stream = controllableBody();
    const aya = { id: "aya", label: "Aya", is_local: true, enabled: true, provider: "ollama" };
    const claude = { id: "claude", label: "Claude", is_local: false, enabled: true, provider: "anthropic" };
    mountPage(buildClient(stream.body, { models: [aya, claude], chatBodies }));

    await screen.findByRole("combobox");
    await waitFor(() => expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("aya"));
    await user.type(await screen.findByPlaceholderText(/ask about this document/i), "hello");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(chatBodies.length).toBe(1));
    expect(chatBodies[0].model).toBe("aya");
    act(() => stream.close());
  });
});
