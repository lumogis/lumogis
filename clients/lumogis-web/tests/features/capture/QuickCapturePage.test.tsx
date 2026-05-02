// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest — QuickCapture (Phase 5E).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { NAV_ITEMS } from "../../../src/components/BottomNav";
import { QuickCapturePage } from "../../../src/features/capture/QuickCapturePage";
import { __setCaptureOutboxBackendForTests } from "../../../src/pwa/captureOutbox";
import { MemoryRouter } from "react-router-dom";

function setNavigatorOnline(on: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    writable: true,
    value: on,
  });
  window.dispatchEvent(new Event(on ? "online" : "offline"));
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function meResponse() {
  return jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" });
}

function pathnameOf(input: RequestInfo): string {
  const s = String(input);
  try {
    return new URL(s).pathname;
  } catch {
    return s;
  }
}

function baseDetail(over: Record<string, unknown> = {}) {
  return {
    id: "cap-1",
    status: "pending",
    capture_type: "text",
    title: null,
    text: "hello",
    url: null,
    tags: null,
    note_id: null,
    source_channel: "lumogis_web",
    last_error: null,
    created_at: "2026-04-30T12:00:00Z",
    updated_at: "2026-04-30T12:00:00Z",
    captured_at: null,
    indexed_at: null,
    attachments: [] as Array<{
      id: string;
      attachment_type: "image" | "audio";
      mime_type: string;
      size_bytes: number;
      original_filename?: string | null;
      processing_status: "stored" | "failed";
      created_at: string;
    }>,
    transcripts: [] as Array<Record<string, unknown>>,
    ...over,
  };
}

let originalFetch: typeof fetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
  setNavigatorOnline(true);
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  __setCaptureOutboxBackendForTests(null);
});

function installOutboxMemory(): void {
  const store = new Map<string, unknown>();
  __setCaptureOutboxBackendForTests({
    get: async (k) => store.get(k),
    set: async (k, v) => {
      store.set(k, v);
    },
    del: async (k) => {
      store.delete(k);
    },
  });
}

function renderPage(client: ApiClient, initialEntries: string[] = ["/capture"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AuthProvider client={client} skipRefreshOnMount>
        <QuickCapturePage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("QuickCapturePage", () => {
  it("schema includes /capture navigation target", () => {
    expect(NAV_ITEMS.some((i) => i.href === "/capture" && i.key === "capture")).toBe(true);
  });

  it("Add to memory disabled before capture exists", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/auth/me")) return meResponse();
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    renderPage(client);

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("quick-capture-add-memory")).toBeDisabled();
  });

  it("shows validation error when saving empty note online (no API call)", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);
    await waitFor(() => expect(screen.getByTestId("quick-capture-page")).toBeInTheDocument());
    await user.click(screen.getByTestId("quick-capture-save-server"));

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-error")).toHaveTextContent(/Enter some text/i);
    });
    const capturePosts = (fetchImpl.mock.calls as [RequestInfo, RequestInit?][]).filter(
      ([u, i]) => pathnameOf(u) === "/api/v1/captures" && i?.method === "POST",
    );
    expect(capturePosts.length).toBe(0);
  });

  it("Add to memory disabled when offline after capture exists", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, baseDetail({ text: "online first" }));
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);
    await waitFor(() => expect(screen.getByPlaceholderText(/short note/i)).toBeInTheDocument());
    await user.type(screen.getByPlaceholderText(/short note/i), "online first");
    await user.click(screen.getByTestId("quick-capture-save-server"));
    await waitFor(() => expect(screen.getByTestId("quick-capture-detail")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("quick-capture-add-memory")).not.toBeDisabled());

    setNavigatorOnline(false);
    await waitFor(() => expect(screen.getByTestId("quick-capture-add-memory")).toBeDisabled());
  });

  it("creates a text capture and shows detail", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const detailEmpty = baseDetail({ text: "My note" });
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, detailEmpty);
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await waitFor(() => expect(screen.getByRole("heading", { name: /quick capture/i })).toBeInTheDocument());

    await user.type(screen.getByPlaceholderText(/short note/i), "My note");
    await user.click(screen.getByTestId("quick-capture-save-server"));

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-detail")).toBeInTheDocument();
      expect(screen.queryByTestId("quick-capture-attachments-locked")).toBeNull();
    });
    expect(screen.getByTestId("quick-capture-add-memory")).not.toBeDisabled();
  });

  it("uploads an image when capture exists", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let detailState = baseDetail({ text: "pic" });
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1/attachments" && init?.method === "POST") {
        detailState = {
          ...detailState,
          capture_type: "photo",
          attachments: [
            {
              id: "att-img",
              attachment_type: "image",
              mime_type: "image/png",
              size_bytes: 120,
              original_filename: "x.png",
              processing_status: "stored",
              created_at: "2026-04-30T12:01:00Z",
            },
          ],
        };
        return jsonResponse(201, {
          id: "att-img",
          attachment_type: "image",
          mime_type: "image/png",
          size_bytes: 120,
          original_filename: "x.png",
          processing_status: "stored",
          created_at: "2026-04-30T12:01:00Z",
        });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, detailState);
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await waitFor(() => expect(screen.getByPlaceholderText(/short note/i)).toBeInTheDocument());
    await user.type(screen.getByPlaceholderText(/short note/i), "pic");
    await user.click(screen.getByTestId("quick-capture-save-server"));

    await waitFor(() => expect(screen.getByLabelText(/image \(jpeg/i)).toBeInTheDocument());

    const file = new File([new Uint8Array([1, 2, 3])], "test.png", { type: "image/png" });
    await user.upload(screen.getByLabelText(/image \(jpeg/i), file);

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-attachment-list").textContent).toContain("image");
    });
  });

  it("uploads audio file and transcribes (mocked)", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let phase: "new" | "audio" | "tr" = "new";
    const empty = baseDetail({ text: "voice" });
    const withAudio = baseDetail({
      text: "voice",
      capture_type: "voice",
      attachments: [
        {
          id: "att-audio",
          attachment_type: "audio",
          mime_type: "audio/webm",
          size_bytes: 100,
          original_filename: "x.webm",
          processing_status: "stored",
          created_at: "2026-04-30T12:02:00Z",
        },
      ],
    });
    const withTr = {
      ...withAudio,
      transcripts: [
        {
          id: "tr-1",
          attachment_id: "att-audio",
          transcript_status: "complete",
          transcript_text: "hello world",
          transcript_provenance: "server_stt",
          language: null,
          confidence: null,
          created_at: "2026-04-30T12:03:00Z",
          updated_at: "2026-04-30T12:03:00Z",
        },
      ],
    };

    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1/attachments" && init?.method === "POST") {
        phase = "audio";
        return jsonResponse(201, {
          id: "att-audio",
          attachment_type: "audio",
          mime_type: "audio/webm",
          size_bytes: 100,
          original_filename: "x.webm",
          processing_status: "stored",
          created_at: "2026-04-30T12:02:00Z",
        });
      }
      if (path === "/api/v1/captures/cap-1/transcribe" && init?.method === "POST") {
        phase = "tr";
        return jsonResponse(200, {
          id: "tr-1",
          attachment_id: "att-audio",
          transcript_status: "complete",
          transcript_text: "hello world",
          transcript_provenance: "server_stt",
          created_at: "2026-04-30T12:03:00Z",
          updated_at: "2026-04-30T12:03:00Z",
        });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        if (phase === "tr") return jsonResponse(200, withTr);
        if (phase === "audio") return jsonResponse(200, withAudio);
        return jsonResponse(200, empty);
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await user.type(screen.getByPlaceholderText(/short note/i), "voice");
    await user.click(screen.getByTestId("quick-capture-save-server"));

    await waitFor(() =>
      expect(screen.getByLabelText(/audio file → server/i)).toBeInTheDocument(),
    );
    const audioFile = new File([new Uint8Array([1])], "clip.webm", { type: "audio/webm" });
    await user.upload(screen.getByLabelText(/audio file → server/i), audioFile);

    await waitFor(() => expect(screen.getByTestId("quick-capture-transcribe-btn")).not.toBeDisabled());

    await user.click(screen.getByTestId("quick-capture-transcribe-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-transcript-list").textContent).toContain("hello world");
    });
    expect(screen.getByTestId("quick-capture-add-memory")).not.toBeDisabled();
  });

  it("shows a helpful message when transcription returns 503", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let phase: "new" | "audio" = "new";
    const empty = baseDetail({ text: "a" });
    const withAudio = baseDetail({
      text: "a",
      attachments: [
        {
          id: "att-audio",
          attachment_type: "audio",
          mime_type: "audio/webm",
          size_bytes: 50,
          processing_status: "stored",
          created_at: "2026-04-30T12:02:00Z",
        },
      ],
    });

    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1/attachments" && init?.method === "POST") {
        phase = "audio";
        return jsonResponse(201, {
          id: "att-audio",
          attachment_type: "audio",
          mime_type: "audio/webm",
          size_bytes: 50,
          processing_status: "stored",
          created_at: "2026-04-30T12:02:00Z",
        });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, phase === "audio" ? withAudio : empty);
      }
      if (path.includes("/transcribe") && init?.method === "POST") {
        return jsonResponse(503, {
          detail: { code: "stt_disabled", message: "disabled" },
        });
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await user.type(screen.getByPlaceholderText(/short note/i), "a");
    await user.click(screen.getByTestId("quick-capture-save-server"));
    const audioFile = new File([new Uint8Array([1])], "clip.webm", { type: "audio/webm" });
    await waitFor(() =>
      expect(screen.getByLabelText(/audio file → server/i)).toBeInTheDocument(),
    );
    await user.upload(screen.getByLabelText(/audio file → server/i), audioFile);

    await waitFor(() => expect(screen.getByTestId("quick-capture-transcribe-btn")).not.toBeDisabled());
    expect(screen.getByTestId("quick-capture-add-memory")).toBeDisabled();

    await user.click(screen.getByTestId("quick-capture-transcribe-btn"));

    await waitFor(() => {
      const err = screen.getByTestId("quick-capture-error");
      expect(err.textContent).toMatch(/transcription is unavailable/i);
    });
  });

  it("Add to memory POSTs …/index and refreshes detail", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const pendingDetail = baseDetail({ text: "ix" });
    const indexedDetail = baseDetail({
      text: "ix",
      status: "indexed",
      note_id: "n1",
      indexed_at: "2026-04-30T12:10:00Z",
    });
    let indexed = false;
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1/index" && init?.method === "POST") {
        indexed = true;
        return jsonResponse(200, indexedDetail);
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, indexed ? indexedDetail : pendingDetail);
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);
    await user.type(screen.getByPlaceholderText(/short note/i), "ix");
    await user.click(screen.getByTestId("quick-capture-save-server"));
    await waitFor(() => expect(screen.getByTestId("quick-capture-add-memory")).not.toBeDisabled());

    await user.click(screen.getByTestId("quick-capture-add-memory"));

    await waitFor(() => {
      expect(
        (fetchImpl.mock.calls as [RequestInfo, RequestInit?][]).some(
          ([u, i]) => pathnameOf(u) === "/api/v1/captures/cap-1/index" && i?.method === "POST",
        ),
      ).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-info").textContent).toMatch(/added to memory/i);
    });
  });

  it("Add to memory surfaces transcript-required error from API", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1/index" && init?.method === "POST") {
        return jsonResponse(422, { detail: { error: "capture_transcript_required" } });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, baseDetail({ text: "hello" }));
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);
    await user.type(screen.getByPlaceholderText(/short note/i), "hello");
    await user.click(screen.getByTestId("quick-capture-save-server"));
    await waitFor(() => expect(screen.getByTestId("quick-capture-add-memory")).not.toBeDisabled());

    await user.click(screen.getByTestId("quick-capture-add-memory"));

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-error").textContent).toMatch(/transcrib|add to memory/i);
    });
  });

  it("Add to memory surfaces memory-index failure", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const pending = baseDetail({ text: "ix" });
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1/index" && init?.method === "POST") {
        return jsonResponse(503, { detail: { error: "index_memory_unavailable" } });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, pending);
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);
    await user.type(screen.getByPlaceholderText(/short note/i), "ix");
    await user.click(screen.getByTestId("quick-capture-save-server"));
    await waitFor(() => expect(screen.getByTestId("quick-capture-add-memory")).not.toBeDisabled());
    await user.click(screen.getByTestId("quick-capture-add-memory"));

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-error").textContent).toMatch(/memory|index|unavailable/i);
    });
  });

  it("shows file upload path when MediaRecorder is missing", async () => {
    vi.stubGlobal("MediaRecorder", undefined);

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    renderPage(client);

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-recorder-disabled")).toHaveTextContent(/not supported/i);
    });
  });

  it("does not call create capture when offline hook reports false (server save)", async () => {
    setNavigatorOnline(false);
    installOutboxMemory();

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      return jsonResponse(500, { detail: "should not hit" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await waitFor(() => expect(screen.getByTestId("quick-capture-offline")).toBeInTheDocument());
    expect(screen.getByTestId("quick-capture-add-memory")).toBeDisabled();
    await user.type(screen.getByPlaceholderText(/short note/i), "x");
    expect(screen.getByTestId("quick-capture-save-server")).toBeDisabled();

    const capturePosts = (fetchImpl.mock.calls as [RequestInfo, RequestInit?][]).filter(
      ([u, i]) => pathnameOf(u) === "/api/v1/captures" && i?.method === "POST",
    );
    expect(capturePosts.length).toBe(0);
  });

  it("offline text save goes to outbox and does not POST /captures", async () => {
    setNavigatorOnline(false);
    installOutboxMemory();

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      return jsonResponse(500, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await waitFor(() => expect(screen.getByTestId("capture-outbox-stats")).toBeInTheDocument());
    await user.type(screen.getByPlaceholderText(/short note/i), "local only");
    await user.click(screen.getByTestId("quick-capture-save-local"));

    await waitFor(() => {
      expect(screen.getByTestId("capture-outbox-stats").textContent).toMatch(/Pending:\s*1/i);
    });

    const capturePosts = (fetchImpl.mock.calls as [RequestInfo, RequestInit?][]).filter(
      ([u, i]) => pathnameOf(u) === "/api/v1/captures" && i?.method === "POST",
    );
    expect(capturePosts.length).toBe(0);
  });

  it("offline voice file goes to outbox and does not POST /captures", async () => {
    setNavigatorOnline(false);
    installOutboxMemory();

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      return jsonResponse(500, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await waitFor(() => expect(screen.getByTestId("quick-capture-offline-audio-file")).toBeInTheDocument());
    const f = new File([new Uint8Array([1, 2])], "v.webm", { type: "audio/webm" });
    await user.upload(screen.getByTestId("quick-capture-offline-audio-file"), f);

    await waitFor(() => {
      expect(screen.getByTestId("capture-outbox-stats").textContent).toMatch(/Pending:\s*1/i);
      expect(screen.getByTestId("capture-outbox-stats").textContent).toMatch(/Voice clips:\s*1/i);
    });

    const capturePosts = (fetchImpl.mock.calls as [RequestInfo, RequestInit?][]).filter(
      ([u, i]) => pathnameOf(u) === "/api/v1/captures" && i?.method === "POST",
    );
    expect(capturePosts.length).toBe(0);
  });

  it("photo input is disabled when offline", async () => {
    installOutboxMemory();

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    let created = false;
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        created = true;
        return jsonResponse(201, { capture_id: "cap-1", status: "pending" });
      }
      if (path === "/api/v1/captures/cap-1" && init?.method === "GET") {
        return jsonResponse(200, baseDetail({ text: "x" }));
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await waitFor(() => expect(screen.getByPlaceholderText(/short note/i)).toBeInTheDocument());
    await user.type(screen.getByPlaceholderText(/short note/i), "x");
    await user.click(screen.getByTestId("quick-capture-save-server"));
    await waitFor(() => expect(created).toBe(true));

    setNavigatorOnline(false);

    await waitFor(() => expect(screen.getByTestId("quick-capture-image-input")).toBeDisabled());
    expect(screen.getByTestId("quick-capture-add-memory")).toBeDisabled();
  });

  it("Sync now posts client_id; going online alone does not sync", async () => {
    setNavigatorOnline(false);
    installOutboxMemory();

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const bodies: unknown[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const path = pathnameOf(input);
      if (path.includes("/auth/me")) return meResponse();
      if (path === "/api/v1/captures" && init?.method === "POST") {
        bodies.push(JSON.parse(String(init?.body)));
        return jsonResponse(201, { capture_id: "cap-sync", status: "pending" });
      }
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
    const user = userEvent.setup();

    renderPage(client);

    await user.type(screen.getByPlaceholderText(/short note/i), "queued");
    await user.click(screen.getByTestId("quick-capture-save-local"));

    await waitFor(() => expect(screen.getByTestId("quick-capture-sync-outbox")).toBeDisabled());

    setNavigatorOnline(true);

    await waitFor(() => expect(screen.getByTestId("quick-capture-sync-outbox")).not.toBeDisabled());

    expect(bodies.length).toBe(0);

    await user.click(screen.getByTestId("quick-capture-sync-outbox"));

    await waitFor(() => expect(bodies.length).toBe(1));
    expect((bodies[0] as { client_id?: string; text?: string }).text).toBe("queued");
    expect(typeof (bodies[0] as { client_id?: string }).client_id).toBe("string");

    await waitFor(() => {
      expect(screen.getByTestId("capture-outbox-stats").textContent).toMatch(/Pending:\s*0/i);
    });
  });

  it("prefills from query params and rejects non-http(s) shared URL", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      if (String(input).includes("/auth/me")) return meResponse();
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    renderPage(client, ["/capture?title=T1&text=Body%20here&url=https%3A%2F%2Fexample.com%2Fp"]);

    await waitFor(() => {
      expect(screen.getByLabelText(/title \(optional\)/i)).toHaveValue("T1");
      expect(screen.getByPlaceholderText(/short note/i)).toHaveValue("Body here");
      expect(screen.getByLabelText(/url \(optional\)/i)).toHaveValue("https://example.com/p");
    });

    await waitFor(() => {
      expect(screen.getByTestId("quick-capture-info").textContent).toMatch(/prefilled/i);
    });

    const posts = (fetchImpl.mock.calls as [RequestInfo, RequestInit?][]).filter(
      ([u, i]) => pathnameOf(u) === "/api/v1/captures" && i?.method === "POST",
    );
    expect(posts.length).toBe(0);
  });

  it("drops javascript: and other non-http(s) values from shared url param", async () => {
    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      if (String(input).includes("/auth/me")) return meResponse();
      return jsonResponse(404, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    renderPage(client, ["/capture?text=x&url=javascript%3Aalert(1)"]);

    await waitFor(() => expect(screen.getByPlaceholderText(/short note/i)).toHaveValue("x"));
    expect(screen.getByLabelText(/url \(optional\)/i)).toHaveValue("");
  });

  it("offline prefilled query does not POST /captures silently", async () => {
    setNavigatorOnline(false);
    installOutboxMemory();

    const tokens = new AccessTokenStore();
    tokens.set("tok");
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      if (String(input).includes("/auth/me")) return meResponse();
      return jsonResponse(500, { detail: "unexpected" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    renderPage(client, ["/capture?text=FromShare"]);

    await waitFor(() => expect(screen.getByPlaceholderText(/short note/i)).toHaveValue("FromShare"));

    const posts = (fetchImpl.mock.calls as [RequestInfo, RequestInit?][]).filter(
      ([u, i]) => pathnameOf(u) === "/api/v1/captures" && i?.method === "POST",
    );
    expect(posts.length).toBe(0);
  });
});
