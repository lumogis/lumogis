// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { CaptureArchiveView } from "../../../src/features/capture/CaptureArchiveView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "u1", email: "a@home.lan", role: "admin" as const };

function listRow(id: string) {
  return {
    id,
    status: "indexed",
    capture_type: "text",
    title: `archived ${id}`,
    text: `note ${id}`,
    url: null,
    last_error: null,
    attachment_count: 0,
    transcript_count: 0,
    created_at: "2026-07-10T10:00:00Z",
    updated_at: "2026-07-10T10:00:00Z",
  };
}

function detail(id: string) {
  return {
    id,
    status: "indexed",
    capture_type: "text",
    title: `archived ${id}`,
    text: `note ${id}`,
    url: null,
    tags: ["tag1"],
    note_id: "abcd1234-note",
    source_channel: "lumogis_web",
    last_error: null,
    created_at: "2026-07-10T10:00:00Z",
    updated_at: "2026-07-10T10:00:00Z",
    captured_at: null,
    indexed_at: "2026-07-10T11:00:00Z",
    attachments: [],
    transcripts: [],
  };
}

function renderArchive(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <CaptureArchiveView />
    </AuthProvider>,
  );
}

describe("CaptureArchiveView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists indexed captures (status=indexed) and opens a READ-ONLY detail", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/captures/a1") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, detail("a1"));
      }
      if (u.includes("/api/v1/captures") && (!init?.method || init.method === "GET")) {
        expect(u).toContain("status=indexed");
        expect(u).not.toContain("status=pending");
        return jsonResponse(200, { captures: [listRow("a1")], total: 1, limit: 20, offset: 0 });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderArchive(fetchImpl);
    const user = userEvent.setup();
    await user.click(await screen.findByText("archived a1"));

    const panel = await screen.findByLabelText("Committed capture");
    // provenance shown
    expect(within(panel).getByText(/Committed to memory \(note abcd1234/i)).toBeTruthy();
    expect(within(panel).getByText(/Committed 7\/10\/2026/)).toBeTruthy();

    // READ-ONLY guard (ALLOWLIST, not blocklist): the archive detail renders
    // exactly one button — "Close". This catches any differently-named mutate
    // control ("Remove"/"Update"/…) a future change might slip in.
    const panelButtons = within(panel).getAllByRole("button");
    expect(panelButtons).toHaveLength(1);
    expect(panelButtons[0].textContent).toBe("Close");
  });

  it("shows an empty state when nothing is committed", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/captures") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, { captures: [], total: 0, limit: 20, offset: 0 });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderArchive(fetchImpl);
    expect(await screen.findByText(/Nothing committed yet/i)).toBeTruthy();
  });
});
