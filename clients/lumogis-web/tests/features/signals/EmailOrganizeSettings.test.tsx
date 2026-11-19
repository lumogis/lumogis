// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-645 — EmailOrganizeSettings: opt-in toggles, non-Gmail steer, folder picker
// (excludes 'other'), save PATCH, and undo POST.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { EmailOrganizeSettings } from "../../../src/features/signals/EmailOrganizeSettings";
import { jsonResponse } from "../../helpers/jsonResponse";

const TAX = {
  categories: [
    { slug: "bills_finance", label: "Bills & finance", enabled: true, is_custom: false, is_protected: false, sort: 0 },
    { slug: "promotional_bulk", label: "Promotional / bulk", enabled: true, is_custom: false, is_protected: false, sort: 0 },
    { slug: "other", label: "Other", enabled: true, is_custom: false, is_protected: true, sort: 0 },
  ],
};

function renderView(fetchImpl: typeof fetch, labelVisible: boolean) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <EmailOrganizeSettings sourceId="src-1" labelVisible={labelVisible} />
    </AuthProvider>,
  );
}

function baseFetch(extra?: (u: string, init?: RequestInit) => Response | undefined) {
  return vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const u = String(input);
    const hit = extra?.(u, init);
    if (hit) return hit;
    if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
    if (u.includes("/email-taxonomy")) return jsonResponse(200, TAX);
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
}

describe("EmailOrganizeSettings", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows the label caveat on a non-Gmail provider once label is on", async () => {
    renderView(baseFetch(), false);
    fireEvent.click(screen.getByRole("checkbox", { name: /Label my emails/i }));
    await waitFor(() => expect(screen.getByTestId("label-caveat")).toBeInTheDocument());
  });

  it("folder picker excludes the protected 'other' category", async () => {
    renderView(baseFetch(), true);
    fireEvent.click(screen.getByRole("checkbox", { name: /Sort my emails into folders/i }));
    // Wait for the taxonomy query to resolve and the category rows to render.
    expect(await screen.findByRole("checkbox", { name: /Bills & finance/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Promotional/ })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /^Other$/ })).not.toBeInTheDocument();
  });

  it("Save PATCHes the organize opt-ins", async () => {
    let patched: unknown = null;
    const fetchImpl = baseFetch((u, init) => {
      if (u.includes("/organize") && init?.method === "PATCH") {
        patched = JSON.parse(String(init?.body));
        return jsonResponse(202, { updated: true, apply_to_existing: false });
      }
      return undefined;
    });
    renderView(fetchImpl, true);
    fireEvent.click(screen.getByRole("checkbox", { name: /Label my emails/i }));
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(patched).not.toBeNull());
    expect((patched as { organize_label: boolean }).organize_label).toBe(true);
  });

  it("Undo POSTs the reversal", async () => {
    let undone = false;
    const fetchImpl = baseFetch((u, init) => {
      if (u.includes("/organize/undo") && init?.method === "POST") {
        undone = true;
        return jsonResponse(202, { undo_pending: true, scope: "all" });
      }
      return undefined;
    });
    renderView(fetchImpl, true);
    // LUM-729 replaced the single "remove everything" button with a scoped undo,
    // because the folder-only put-back was previously unreachable. "all" is still
    // the default, so this button is still the same reversal.
    fireEvent.click(screen.getByRole("button", { name: /^Undo$/ }));
    await waitFor(() => expect(undone).toBe(true));
  });
});
