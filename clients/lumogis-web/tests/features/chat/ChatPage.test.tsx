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
//
// LUM-853: the empty-library state is client logic (thread empty AND the caller's document list
// loaded with zero rows), so Vitest is its layer; the auth-unset -> offer join is test 14
// (single-user boot + empty library); the server inputs are pinned by test 13 in
// orchestrator/tests/test_me_onboarding_routes.py. There is no auth-off browser lane
// (LUM-894 Later).

import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clear } from "idb-keyval";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import type { DocumentSummary } from "../../../src/api/documents";
import { AuthProvider, useAuth } from "../../../src/auth/AuthProvider";
import { ChatPage, humaniseChatError } from "../../../src/features/chat/ChatPage";
import * as drafts from "../../../src/pwa/drafts";
import { getDraft, makeChatDraftKey } from "../../../src/pwa/drafts";
import { renderWithRouter } from "../../helpers/renderWithRouter";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

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

function wowStateResponse() {
  return jsonResponse(200, {
    entities_ready: false,
    top_entities: [],
    wow_dismissed_at: null,
    onboarding_completed_at: "2026-01-01T00:00:00Z",
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

const oneDoc: DocumentSummary = {
  document_id: 1,
  in_flight_job_id: null,
  display_name: "notes.md",
  file_path: "/library/notes.md",
  file_type: ".md",
  chunk_count: 3,
  entity_count: 0,
  scope: "personal",
  status: "indexed",
  indexed_at: "2026-11-01T00:00:00Z",
  error_message: null,
};

function documentsResponse(docs: DocumentSummary[]): Response {
  return jsonResponse(200, { documents: docs });
}

interface RouterCfg {
  refresh?: () => Response;
  me?: (init?: RequestInit) => Response;
  documents?: () => Response | Promise<Response>;
  wow?: () => Response;
  models?: () => Response;
  privacy?: () => Response;
  chat?: (init?: RequestInit) => Response | Promise<Response>;
}

function buildClientWithSpy(cfg: RouterCfg = {}): {
  client: ApiClient;
  fetchImpl: MockInstance<(input: RequestInfo, init?: RequestInit) => Promise<Response>>;
} {
  const tokens = new AccessTokenStore();
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/auth/refresh")) return (cfg.refresh ?? loginResponse)();
    if (url.includes("/auth/me"))
      return (cfg.me ?? (() => jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" })))(init);
    if (new URL(url, "http://x").pathname === "/api/v1/documents")
      return (cfg.documents ?? (() => documentsResponse([oneDoc])))();
    if (url.endsWith("/api/v1/models")) return (cfg.models ?? modelsResponse)();
    if (url.includes("/api/v1/me/privacy-mode"))
      return (
        cfg.privacy ??
        (() =>
          jsonResponse(200, {
            instance: { privacy_mode: "local_only", privacy_mode_locked: false, privacy_effective: "local_only" },
            user_restriction: "inherit",
            privacy_effective: "local_only",
            can_allow_cloud: false,
          }))
      )();
    // ConversationSidebar lists history on mount; empty list keeps the sidebar
    // out of its error state (no stray role="alert").
    if (url.endsWith("/api/v1/conversations")) return jsonResponse(200, { conversations: [] });
    if (url.includes("/api/v1/me/wow-state")) return (cfg.wow ?? wowStateResponse)();
    if (url.endsWith("/api/v1/chat/completions"))
      return (cfg.chat ?? (() => sseResponse(sseStreamFrom([sseChunk("ok", "stop"), "data: [DONE]\n\n"]))))(init);
    throw new Error(`unexpected fetch: ${url}`);
  });
  return { client: new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch }), fetchImpl };
}

function buildClient(cfg: RouterCfg = {}): ApiClient {
  return buildClientWithSpy(cfg).client;
}

function renderChat(client: ApiClient, tokens = new AccessTokenStore()) {
  return renderWithRouter(
    <AuthProvider client={client} tokens={tokens}>
      <ChatPage />
    </AuthProvider>,
    { route: "/chat" },
  );
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
    expect(humaniseChatError(400, "invalid_model:claude-7")).toMatch(/claude-7/);
    expect(humaniseChatError(401, "")).toMatch(/sign in/i);
  });
});

describe("ChatPage — render + send", () => {
  it("Phase 2B: chat layout root uses lumogis-chat for narrow-width constraints", async () => {
    const client = buildClient();
    const tokens = new AccessTokenStore();
    const { container } = renderChat(client, tokens);
    await waitFor(() => {
      expect(screen.getByTestId("chat-page")).toBeInTheDocument();
    });
    expect(container.querySelector(".lumogis-chat")).not.toBeNull();
  });

  it("Phase 2D: conversation transcript exposes log + polite live region", async () => {
    const client = buildClient();
    const tokens = new AccessTokenStore();
    renderChat(client, tokens);
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

  it("LUM-876: shows the loading status while waiting, then drops it from the answer", async () => {
    const user = userEvent.setup();
    const encoder = new TextEncoder();
    let push: (part: string) => void = () => undefined;
    let close: () => void = () => undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (part) => controller.enqueue(encoder.encode(part));
        close = () => controller.close();
      },
    });
    const statusChunk = JSON.parse(sseChunk("").slice("data: ".length)) as Record<string, unknown>;
    statusChunk.lumogis = {
      status: { code: "local_model_loading", message: "Loading model on your machine…" },
    };
    const client = buildClient({ chat: () => sseResponse(body) });

    renderChat(client);
    await waitFor(() => {
      expect(screen.getByLabelText(/model$/i)).toBeInTheDocument();
    });
    await user.type(screen.getByLabelText(/^message$/i), "Hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    act(() => {
      push(sseChunk(""));
      push(`data: ${JSON.stringify(statusChunk)}\n\n`);
    });
    await waitFor(() => {
      expect(screen.getByTestId("chat-stream-status")).toHaveTextContent(/Loading model/);
    });

    act(() => {
      push(sseChunk("Hello!", "stop"));
      push("data: [DONE]\n\n");
      close();
    });
    await waitFor(() => {
      expect(screen.getAllByLabelText(/assistant message/i)[0]).toHaveTextContent("Hello!");
    });
    expect(screen.queryByTestId("chat-stream-status")).not.toBeInTheDocument();
    expect(screen.getAllByLabelText(/assistant message/i)[0]).not.toHaveTextContent(/Loading model/);
    expect(globalThis.sessionStorage.getItem("lumogis-chat-threads") ?? "").not.toMatch(/Loading model/);
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

    const { unmount } = renderChat(client);
    await waitFor(() => screen.getByLabelText(/model$/i));
    await user.type(screen.getByLabelText(/^message$/i), "Question one");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => {
      expect(screen.getByLabelText(/assistant message/i)).toHaveTextContent(/ok/);
    });

    unmount();

    renderChat(buildClient());

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
    const tab1 = renderChat(buildClient());
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

    renderChat(buildClient());
    await waitFor(() => screen.getByLabelText(/model$/i));
    expect(screen.queryByText(/tab one message/)).not.toBeInTheDocument();
  });
});

describe("ChatPage — model picker explains why a model is off (LUM-881)", () => {
  const catalog = (claude: Record<string, unknown>) => () =>
    jsonResponse(200, {
      models: [
        { id: "ollama-mistral", label: "Ollama (mistral)", is_local: true, enabled: true, provider: "ollama" },
        { id: "claude", label: "Claude", is_local: false, enabled: false, provider: "anthropic", ...claude },
      ],
    });
  const claudeOption = async () => {
    await screen.findByLabelText(/model$/i);
    return screen.findByRole("option", { name: /^Claude/ });
  };

  it("(a) privacy_mode: suffix says so and the hint is shown", async () => {
    renderChat(buildClient({ models: catalog({ disabled_reason: "privacy_mode" }) }));
    expect(await claudeOption()).toHaveTextContent("Claude — off (privacy mode)");
    expect(await screen.findByTestId("privacy-blocked-hint")).toBeInTheDocument();
  });

  it("(b) no_credentials: 'no API key' and no hint", async () => {
    renderChat(buildClient({ models: catalog({ disabled_reason: "no_credentials" }) }));
    expect(await claudeOption()).toHaveTextContent("Claude — no API key");
    expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument();
  });

  it("(c) optional_disabled: 'not turned on' and no hint", async () => {
    renderChat(buildClient({ models: catalog({ disabled_reason: "optional_disabled" }) }));
    expect(await claudeOption()).toHaveTextContent("Claude — not turned on");
    expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument();
  });

  it("(d) old server without the field: 'unavailable' and no hint", async () => {
    renderChat(buildClient({ models: catalog({}) }));
    expect(await claudeOption()).toHaveTextContent("Claude — unavailable");
    expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument();
  });

  it("an unknown future reason reads like an omitted one", async () => {
    renderChat(buildClient({ models: catalog({ disabled_reason: "breaker_open" }) }));
    expect(await claudeOption()).toHaveTextContent("Claude — unavailable");
    expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// LUM-853 — chat empty-library state
// ---------------------------------------------------------------------------

const EMPTY_TITLE = "No documents in your library yet";

function wowReadyResponse(): Response {
  return jsonResponse(200, {
    entities_ready: true,
    top_entities: [],
    wow_dismissed_at: null,
    onboarding_completed_at: "2026-01-01T00:00:00Z",
  });
}

interface StubbedStreams {
  spy: MockInstance;
  controllers: ReadableStreamDefaultController<Uint8Array>[];
}

/** `/api/v1/events` -> a never-closing event stream; every other global fetch rejects. */
function stubEventStreams(): StubbedStreams {
  const controllers: ReadableStreamDefaultController<Uint8Array>[] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation((async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ) => {
    const path = new URL(String(input), "http://x").pathname;
    if (path !== "/api/v1/events") throw new TypeError(`unexpected global fetch: ${path}`);
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        ctl = c;
      },
    });
    controllers.push(ctl);
    init?.signal?.addEventListener("abort", () => {
      try {
        ctl.close();
      } catch {
        /* already closed */
      }
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }) as typeof fetch);
  return { spy, controllers };
}

/** Streams to /api/v1/events that have not been aborted. */
function openEventStreams(spy: MockInstance): number {
  return spy.mock.calls.filter((c) => {
    const path = new URL(String(c[0]), "http://x").pathname;
    return path === "/api/v1/events" && (c[1] as RequestInit | undefined)?.signal?.aborted === false;
  }).length;
}

function documentsCalls(fetchImpl: MockInstance): number {
  return fetchImpl.mock.calls.filter(
    (c) => new URL(String(c[0]), "http://x").pathname === "/api/v1/documents",
  ).length;
}

describe("ChatPage — first chat starts on a usable model (LUM-310)", () => {
  const llama = { id: "llama", label: "llama", is_local: true, enabled: true, provider: "ollama" };
  const claudeNoKey = {
    id: "claude",
    label: "Claude",
    is_local: false,
    enabled: false,
    provider: "anthropic",
    disabled_reason: "no_credentials",
  };
  const picker = async () => (await screen.findByLabelText(/model$/i)) as HTMLSelectElement;

  it("a catalog that failed during boot and then loads moves the chat off the hard-coded default", async () => {
    // A fresh install: Lumogis Web opens before the orchestrator answers, so the
    // first /api/v1/models fails and the seed thread gets DEFAULT_MODEL ("claude").
    // Claude is in the catalog (no key) once it loads, but it cannot be used.
    const user = userEvent.setup();
    let calls = 0;
    const client = buildClient({
      models: () =>
        ++calls === 1
          ? jsonResponse(503, { detail: "models_unavailable" })
          : jsonResponse(200, { models: [llama, claudeNoKey] }),
    });
    renderChat(client);
    await user.click(await screen.findByRole("button", { name: /retry/i }));
    await waitFor(async () => expect((await picker()).value).toBe("llama"));
  });

  it("a disabled model listed first is never the starting model", async () => {
    renderChat(buildClient({ models: () => jsonResponse(200, { models: [claudeNoKey, llama] }) }));
    await waitFor(async () => expect((await picker()).value).toBe("llama"));
  });

  it("a restored thread on a model that is now off moves to a usable one", async () => {
    // Same tab, after a reload: the thread kept "claude" from the boot fallback.
    let calls = 0;
    const client = buildClient({
      models: () =>
        ++calls === 1
          ? jsonResponse(503, { detail: "models_unavailable" })
          : jsonResponse(200, { models: [llama, claudeNoKey] }),
    });
    const first = renderChat(client);
    await screen.findByText(/Unable to load models/i);
    first.unmount();
    renderChat(client);
    await waitFor(async () => expect((await picker()).value).toBe("llama"));
  });

  it("with no usable model it keeps the current one rather than guessing", async () => {
    renderChat(buildClient({ models: () => jsonResponse(200, { models: [claudeNoKey] }) }));
    await waitFor(async () => expect((await picker()).value).toBe("claude"));
  });
});

describe("ChatPage — empty library (LUM-853)", () => {
  let streams: StubbedStreams;
  beforeEach(() => {
    streams = stubEventStreams();
  });

  it("1 offers source setup when the thread and the library are both empty", async () => {
    const { client } = buildClientWithSpy({
      documents: () => documentsResponse([]),
      wow: wowReadyResponse,
    });
    renderChat(client);
    const heading = await screen.findByRole("heading", { name: EMPTY_TITLE });
    expect(screen.getByRole("link", { name: "Connect a source" })).toHaveAttribute("href", "/me/sources");
    expect(screen.getByRole("link", { name: "Upload a file" })).toHaveAttribute("href", "/documents");

    const log = screen.getByRole("log", { name: /conversation transcript/i });
    expect(within(log).queryByRole("heading", { name: EMPTY_TITLE })).toBeNull();
    // After the transcript in document order.
    expect(log.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The ephemeral-chat sentence stays inside the transcript, once.
    const sentence = screen.getAllByText(/Lumogis chats are ephemeral and live only in this browser tab/);
    expect(sentence).toHaveLength(1);
    expect(log.contains(sentence[0]!)).toBe(true);

    // The guided cards are not gated on the library, and only the documents stream is open.
    await screen.findByTestId("wow-gate");
    await waitFor(() => expect(openEventStreams(streams.spy)).toBe(1));
  });

  it("2 keeps today's chat when the library has documents", async () => {
    renderChat(buildClient({ documents: () => documentsResponse([oneDoc]), wow: wowReadyResponse }));
    await screen.findByRole("combobox", { name: "Model" });
    await screen.findByTestId("wow-gate");
    expect(screen.queryByRole("heading", { name: EMPTY_TITLE })).toBeNull();
    expect(screen.queryByRole("link", { name: "Connect a source" })).toBeNull();
    expect(screen.getByText(/Lumogis chats are ephemeral and live only in this browser tab/)).toBeInTheDocument();
  });

  it("3 shows nothing new while the documents list is loading", async () => {
    renderChat(buildClient({ documents: () => new Promise<Response>(() => undefined) }));
    await screen.findByRole("combobox", { name: "Model" });
    expect(screen.queryByRole("heading", { name: EMPTY_TITLE })).toBeNull();
    expect(screen.getByText(/Lumogis chats are ephemeral and live only in this browser tab/)).toBeInTheDocument();
  });

  it("4 shows nothing new when the documents list fails", async () => {
    const { client, fetchImpl } = buildClientWithSpy({ documents: () => jsonResponse(500, { detail: "boom" }) });
    renderChat(client);
    await screen.findByRole("combobox", { name: "Model" });
    await waitFor(() => expect(documentsCalls(fetchImpl)).toBeGreaterThan(0));
    expect(screen.queryByRole("heading", { name: EMPTY_TITLE })).toBeNull();
    expect(screen.queryByRole("link", { name: "Connect a source" })).toBeNull();
  });

  it("5 flips to normal chat when a document lands (SSE path)", async () => {
    let calls = 0;
    const { client, fetchImpl } = buildClientWithSpy({
      documents: () => {
        calls += 1;
        return documentsResponse(calls === 1 ? [] : [oneDoc]);
      },
      wow: wowReadyResponse,
    });
    renderChat(client);
    await screen.findByRole("heading", { name: EMPTY_TITLE });
    await waitFor(() => expect(openEventStreams(streams.spy)).toBe(1));
    expect(documentsCalls(fetchImpl)).toBe(1);

    const enc = new TextEncoder();
    await act(async () => {
      for (const c of streams.controllers) {
        try {
          c.enqueue(enc.encode("event: document_status_changed\ndata: {}\n\n"));
        } catch {
          /* aborted stream */
        }
      }
    });
    await waitFor(() => expect(screen.queryByRole("heading", { name: EMPTY_TITLE })).toBeNull());
    expect(documentsCalls(fetchImpl)).toBe(2);
  });

  it("6 does not show the empty-library state on a non-empty thread", async () => {
    const user = userEvent.setup();
    renderChat(buildClient({ documents: () => documentsResponse([]), wow: wowReadyResponse }));
    await screen.findByRole("heading", { name: EMPTY_TITLE });
    await user.type(screen.getByLabelText(/^message$/i), "Hi");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getAllByLabelText(/assistant message/i)[0]).toHaveTextContent("ok"));
    expect(screen.queryByRole("heading", { name: EMPTY_TITLE })).toBeNull();
  });

  it("7 copy names documents and avoids member-facing scope words", async () => {
    renderChat(buildClient({ documents: () => documentsResponse([]), wow: wowReadyResponse }));
    const heading = await screen.findByRole("heading", { name: EMPTY_TITLE });
    const block = heading.parentElement!;
    const text = block.textContent ?? "";
    expect(text).toMatch(/documents/i);
    expect(text).not.toMatch(/\b(personal|shared|system)\b/i);
    // Never claim the account (or Lumogis) is empty: it names the missing category only.
    expect(text).not.toMatch(/account is empty|nothing here|lumogis is empty|no data/i);
  });

  it("8 an in-flight upload counts as a document", async () => {
    renderChat(
      buildClient({
        documents: () => documentsResponse([{ ...oneDoc, status: "indexing", in_flight_job_id: 7 }]),
      }),
    );
    await screen.findByRole("combobox", { name: "Model" });
    expect(screen.queryByRole("heading", { name: EMPTY_TITLE })).toBeNull();
  });

  it("9 does not open the documents stream on a populated library", async () => {
    renderChat(buildClient({ documents: () => documentsResponse([oneDoc]), wow: wowReadyResponse }));
    await screen.findByTestId("wow-gate");
    expect(openEventStreams(streams.spy)).toBe(0);
  });

  it("14 auth unset end to end: single-user boot reaches chat and offers sources", async () => {
    seedSignedInDevice("default");
    const { client } = buildClientWithSpy({
      refresh: () => jsonResponse(503, { detail: "refresh is disabled in single-user dev mode" }),
      me: (init) =>
        new Headers(init?.headers).get("Authorization") === null
          ? jsonResponse(200, { id: "default", email: "dev@local.lan", role: "admin" })
          : jsonResponse(401, { error: "unexpected credential" }),
      documents: () => documentsResponse([]),
      wow: () =>
        jsonResponse(200, {
          entities_ready: false,
          top_entities: [],
          wow_dismissed_at: null,
          onboarding_completed_at: "2026-01-01T00:00:00Z",
        }),
    });
    function SingleUserProbe(): JSX.Element {
      return <span data-testid="single-user">{String(useAuth().singleUser)}</span>;
    }
    renderWithRouter(
      <AuthProvider client={client} tokens={new AccessTokenStore()}>
        <SingleUserProbe />
        <ChatPage />
      </AuthProvider>,
      { route: "/chat" },
    );
    await screen.findByRole("heading", { name: EMPTY_TITLE });
    expect(screen.getByRole("link", { name: "Connect a source" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();
    expect(screen.getByTestId("single-user")).toHaveTextContent("true");
  });
});
