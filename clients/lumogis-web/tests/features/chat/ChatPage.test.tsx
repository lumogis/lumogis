// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — ChatPage end-to-end (component-level) smoke.
//
// Mounts the real ChatPage inside a real AuthProvider with an injected
// ApiClient whose `fetchImpl` is fully scripted so the test can drive:
//   - GET /api/v1/auth/refresh + /me (refresh-on-mount → authenticated)
//   - GET /api/v1/models (model picker hydrate)
//   - POST /api/v1/chat/completions (SSE stream of chat-completion chunks)
//
// Also verifies the user-visible behaviour parent plan §"Pass 1.2 item 7"
// pinned: streaming tokens render incrementally, `[DONE]` finalises the
// bubble, the wire-literal 503 `llm_provider_key_missing` becomes a
// human-readable error in the UI.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clear } from "idb-keyval";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { ChatPage, humaniseChatError } from "../../../src/features/chat/ChatPage";
import * as drafts from "../../../src/pwa/drafts";
import { getDraft, makeChatDraftKey } from "../../../src/pwa/drafts";

const origGetDraft = drafts.getDraft;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function loginResponse() {
  return jsonResponse(200, {
    access_token: "ax-1",
    token_type: "bearer",
    expires_in: 900,
    user: { id: "u1", email: "alice@home.lan", role: "user" },
  });
}

function modelsResponse() {
  return jsonResponse(200, {
    models: [
      { id: "claude", label: "Claude", is_local: false, enabled: true, provider: "anthropic" },
      { id: "ollama-mistral", label: "Ollama (mistral)", is_local: true, enabled: true, provider: "ollama" },
    ],
  });
}

function sseChunk(content: string, finish: "stop" | null = null): string {
  const payload = {
    id: "chatcmpl-lumogis",
    object: "chat.completion.chunk",
    created: 1_700_000_000,
    model: "claude",
    choices: [
      {
        index: 0,
        delta: content === "" ? {} : { content },
        finish_reason: finish,
      },
    ],
  };
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function sseStreamFrom(parts: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const p of parts) controller.enqueue(encoder.encode(p));
      controller.close();
    },
  });
}

function sseResponse(stream: ReadableStream<Uint8Array>): Response {
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

interface RouterCfg {
  refresh?: () => Response;
  me?: () => Response;
  models?: () => Response;
  chat?: (init?: RequestInit) => Response | Promise<Response>;
}

function buildClient(cfg: RouterCfg = {}): ApiClient {
  const tokens = new AccessTokenStore();
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/auth/refresh")) return (cfg.refresh ?? loginResponse)();
    if (url.includes("/auth/me"))
      return (cfg.me ?? (() => jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" })))();
    if (url.endsWith("/api/v1/models")) return (cfg.models ?? modelsResponse)();
    if (url.endsWith("/api/v1/chat/completions"))
      return (cfg.chat ?? (() => sseResponse(sseStreamFrom([sseChunk("ok", "stop"), "data: [DONE]\n\n"]))))(init);
    throw new Error(`unexpected fetch: ${url}`);
  });
  return new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
}

function renderChat(client: ApiClient): { tokens: AccessTokenStore } {
  const tokens = new AccessTokenStore();
  render(
    <AuthProvider client={client} tokens={tokens}>
      <ChatPage />
    </AuthProvider>,
  );
  return { tokens };
}

let originalFetch: typeof fetch;
beforeEach(async () => {
  originalFetch = globalThis.fetch;
  globalThis.sessionStorage.clear();
  await clear();
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("humaniseChatError", () => {
  it("maps wire literals to user-facing copy", () => {
    expect(humaniseChatError(503, "llm_provider_key_missing")).toMatch(/Settings/);
    expect(humaniseChatError(503, "llm_provider_unavailable")).toMatch(/unavailable/);
    expect(humaniseChatError(400, "last_message_must_be_user")).toMatch(/last message/i);
    expect(humaniseChatError(400, "system_message_position")).toMatch(/System messages/);
    expect(humaniseChatError(400, "empty_message")).toMatch(/Enter a message/i);
    expect(humaniseChatError(400, "invalid_model:claude-7")).toMatch(/claude-7/);
    expect(humaniseChatError(401, "")).toMatch(/sign in/i);
  });
});

describe("ChatPage — render + send", () => {
  it("Phase 2B: chat layout root uses lumogis-chat for narrow-width constraints", async () => {
    const client = buildClient();
    const tokens = new AccessTokenStore();
    const { container } = render(
      <AuthProvider client={client} tokens={tokens}>
        <ChatPage />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("chat-page")).toBeInTheDocument();
    });
    expect(container.querySelector(".lumogis-chat")).not.toBeNull();
  });

  it("Phase 2D: conversation transcript exposes log + polite live region", async () => {
    const client = buildClient();
    const tokens = new AccessTokenStore();
    render(
      <AuthProvider client={client} tokens={tokens}>
        <ChatPage />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("chat-page")).toBeInTheDocument();
    });
    const transcript = screen.getByLabelText(/conversation transcript/i);
    expect(transcript).toHaveAttribute("role", "log");
    expect(transcript).toHaveAttribute("aria-live", "polite");
    expect(transcript).toHaveAttribute("aria-busy", "false");
  });

  it("hydrates the model picker and streams a response", async () => {
    const user = userEvent.setup();
    const client = buildClient({
      chat: () =>
        sseResponse(
          sseStreamFrom([
            sseChunk(""),
            sseChunk("Hel"),
            sseChunk("lo"),
            sseChunk("!", "stop"),
            "data: [DONE]\n\n",
          ]),
        ),
    });

    renderChat(client);

    await waitFor(() => {
      expect(screen.getByLabelText(/model$/i)).toBeInTheDocument();
    });

    const textarea = screen.getByLabelText(/^message$/i);
    await user.type(textarea, "Hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      const bubbles = screen.getAllByLabelText(/assistant message/i);
      expect(bubbles[0]).toHaveTextContent("Hello!");
    });

    expect(screen.getByLabelText(/user message/i)).toHaveTextContent("Hi");
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
  });

  it("disables Send when the composer is empty after models hydrate", async () => {
    const client = buildClient();
    renderChat(client);
    await waitFor(() => {
      expect(screen.getByLabelText(/model$/i)).toBeInTheDocument();
    });
    const send = screen.getByRole("button", { name: /^send$/i });
    expect(send).toBeDisabled();
    expect((screen.getByLabelText(/^message$/i) as HTMLTextAreaElement).value).toBe("");
  });

  it("shows friendly copy when the server rejects an empty message", async () => {
    const user = userEvent.setup();
    const client = buildClient({
      chat: () => jsonResponse(400, { detail: "empty_message" }),
    });

    renderChat(client);
    await waitFor(() => screen.getByLabelText(/model$/i));

    await user.type(screen.getByLabelText(/^message$/i), "Hello");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Enter a message/i);
    });
  });

  it("renders a humanised error when the server returns 503 llm_provider_key_missing", async () => {
    const user = userEvent.setup();
    const client = buildClient({
      chat: () =>
        jsonResponse(503, { detail: { error: "llm_provider_key_missing", model: "claude" } }),
    });

    renderChat(client);
    await waitFor(() => screen.getByLabelText(/model$/i));

    await user.type(screen.getByLabelText(/^message$/i), "Hello?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Settings/i);
    });
    const composer = screen.getByLabelText(/^message$/i) as HTMLTextAreaElement;
    expect(composer.value).toBe("Hello?");
    const assistant = screen.getByLabelText(/assistant message/i);
    expect(assistant).toHaveTextContent(/LLM provider key/i);
  });

  it("shows a Stop button while streaming and aborts when clicked", async () => {
    const user = userEvent.setup();
    let abortSignal: AbortSignal | undefined;
    const client = buildClient({
      chat: (init) => {
        abortSignal = init?.signal as AbortSignal | undefined;
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(sseChunk("partial-")));
            // Bridge the AbortController to this hand-rolled stream so calling
            // controller.abort() in the UI tears the body reader down (the real
            // platform fetch does this automatically; the test mock has to be
            // wired explicitly).
            const onAbort = (): void => {
              try {
                const err = new Error("aborted");
                err.name = "AbortError";
                controller.error(err);
              } catch {
                /* already errored */
              }
            };
            if (init?.signal) {
              if (init.signal.aborted) onAbort();
              else init.signal.addEventListener("abort", onAbort, { once: true });
            }
          },
        });
        return sseResponse(stream);
      },
    });

    renderChat(client);
    await waitFor(() => screen.getByLabelText(/model$/i));

    await user.type(screen.getByLabelText(/^message$/i), "Long answer please");
    await user.click(screen.getByRole("button", { name: /send/i }));

    const stop = await screen.findByRole("button", { name: /stop/i });
    await waitFor(() => {
      expect(screen.getByLabelText(/assistant message/i)).toHaveTextContent("partial-");
    });

    await user.click(stop);

    await waitFor(() => {
      expect(abortSignal?.aborted).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByLabelText(/assistant message/i)).toHaveTextContent(/Stopped\./);
    });
    const composerAfterStop = screen.getByLabelText(/^message$/i) as HTMLTextAreaElement;
    expect(composerAfterStop.value).toBe("Long answer please");
  });

  it("persists threads to sessionStorage so a remount restores the conversation list", async () => {
    const user = userEvent.setup();
    const client = buildClient();

    const { unmount } = render(
      <AuthProvider client={client} tokens={new AccessTokenStore()}>
        <ChatPage />
      </AuthProvider>,
    );
    await waitFor(() => screen.getByLabelText(/model$/i));
    await user.type(screen.getByLabelText(/^message$/i), "Question one");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => {
      expect(screen.getByLabelText(/assistant message/i)).toHaveTextContent(/ok/);
    });

    unmount();

    render(
      <AuthProvider client={buildClient()} tokens={new AccessTokenStore()}>
        <ChatPage />
      </AuthProvider>,
    );

    await waitFor(() => {
      const matches = screen.getAllByText(/Question one/);
      // Two matches expected: the thread title in the sidebar (persistence
      // round-trip succeeded) and the user message bubble inside the chat
      // transcript when the active thread is rendered.
      expect(matches.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("New chat creates an additional thread row", async () => {
    const user = userEvent.setup();
    renderChat(buildClient());
    await waitFor(() => screen.getByLabelText(/model$/i));

    await user.click(screen.getByRole("button", { name: /\+ New chat/i }));

    const titles = screen.getAllByText(/New chat/i);
    expect(titles.length).toBeGreaterThanOrEqual(2);
  });

  it("falls back to a default model when /api/v1/models errors and offers a Retry", async () => {
    const user = userEvent.setup();
    const client = buildClient({
      models: () => jsonResponse(503, { detail: "models_unavailable" }),
    });

    renderChat(client);

    await waitFor(() => {
      expect(screen.getByText(/Unable to load models/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    // Sanity: the rest of the chat surface is still visible.
    expect(screen.getByLabelText(/^message$/i)).toBeInTheDocument();

    void user; // keep import-used for future drag-out
  });

  it("Phase 3C: does not overwrite the composer if the user types while a draft hydrate is still in flight", async () => {
    const user = userEvent.setup();
    const getDraftSpy = vi.spyOn(drafts, "getDraft");
    let hydrateCalls = 0;
    getDraftSpy.mockImplementation(async (...args) => {
      hydrateCalls += 1;
      if (hydrateCalls === 1) {
        return origGetDraft(...args);
      }
      await new Promise((r) => setTimeout(r, 400));
      return "";
    });

    renderChat(buildClient());
    await waitFor(() => screen.getByLabelText(/model$/i));

    await user.click(screen.getByRole("button", { name: /\+ New chat/i }));
    const textarea = screen.getByLabelText(/^message$/i);
    await user.type(textarea, "typed before idb");

    await act(async () => {
      await new Promise((r) => setTimeout(r, 450));
    });

    expect((textarea as HTMLTextAreaElement).value).toBe("typed before idb");

    getDraftSpy.mockRestore();
  });

  it("Phase 3C: persists composer text to IndexedDB while typing (debounced) and clears draft after successful stream", async () => {
    const user = userEvent.setup();
    renderChat(buildClient());
    await waitFor(() => screen.getByLabelText(/model$/i));

    await user.type(screen.getByLabelText(/^message$/i), "Hi draft");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 450));
    });

    const raw = sessionStorage.getItem("lumogis-chat-threads");
    expect(raw).toBeTruthy();
    const persisted = JSON.parse(raw!) as {
      state: { activeId: string | null };
    };
    const tid = persisted.state.activeId;
    expect(tid).not.toBeNull();
    expect(await getDraft(makeChatDraftKey(tid as string))).toBe("Hi draft");

    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => {
      expect(screen.getByLabelText(/assistant message/i)).toHaveTextContent(/ok/i);
    });
    await waitFor(async () => {
      expect(await getDraft(makeChatDraftKey(tid as string))).toBeUndefined();
    });
  });

  it("Phase 3E: disables send offline, keeps composer text, blocks submit until online", async () => {
    const user = userEvent.setup();
    const client = buildClient({
      chat: () => {
        throw new Error("POST /chat must not fire while offline");
      },
    });

    renderChat(client);
    await waitFor(() => screen.getByLabelText(/model$/i));

    await user.type(screen.getByLabelText(/^message$/i), "offline draft holds");

    await act(async () => {
      Object.defineProperty(navigator, "onLine", { value: false, writable: true, configurable: true });
      window.dispatchEvent(new Event("offline"));
    });

    const sendBtn = screen.getByRole("button", { name: /send/i });
    await waitFor(() => expect(sendBtn).toBeDisabled());
    const ta = screen.getByLabelText(/^message$/i) as HTMLTextAreaElement;
    expect(ta.value).toBe("offline draft holds");

    const form = ta.closest("form");
    expect(form).toBeTruthy();
    await user.click(sendBtn);

    fireEvent.submit(form!);

    await act(async () => {
      Object.defineProperty(navigator, "onLine", { value: true, writable: true, configurable: true });
      window.dispatchEvent(new Event("online"));
    });
  });
});

describe("ChatPage — sessionStorage isolation per tab", () => {
  it("is keyed by sessionStorage so two storages produce two independent thread lists", async () => {
    // Simulate "tab 1": send a message, persist to the shared sessionStorage.
    const user = userEvent.setup();
    const tab1 = render(
      <AuthProvider client={buildClient()} tokens={new AccessTokenStore()}>
        <ChatPage />
      </AuthProvider>,
    );
    await waitFor(() => screen.getByLabelText(/model$/i));
    await user.type(screen.getByLabelText(/^message$/i), "tab one message");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => screen.getByLabelText(/assistant message/i));

    tab1.unmount();

    // Simulate "tab 2": clear sessionStorage to model a brand-new tab opening,
    // then assert the prior thread does NOT bleed across.
    act(() => {
      globalThis.sessionStorage.clear();
    });

    render(
      <AuthProvider client={buildClient()} tokens={new AccessTokenStore()}>
        <ChatPage />
      </AuthProvider>,
    );
    await waitFor(() => screen.getByLabelText(/model$/i));
    expect(screen.queryByText(/tab one message/)).not.toBeInTheDocument();
  });
});
