// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-629 — MeEmailLabelsView: lists categories, adds a custom label, reclassifies.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeEmailLabelsView } from "../../../src/features/me/MeEmailLabelsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

const DEFAULTS = {
  categories: [
    { slug: "school", label: "School", enabled: true, is_custom: false, is_protected: false, sort: 0 },
    { slug: "other", label: "Other", enabled: true, is_custom: false, is_protected: true, sort: 0 },
  ],
};

function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <MeEmailLabelsView />
    </AuthProvider>,
  );
}

describe("MeEmailLabelsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists categories and flags the protected floor", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-taxonomy")) return jsonResponse(200, DEFAULTS);
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;

    renderView(fetchImpl);
    await waitFor(() => expect(screen.getByLabelText("Reclassify School")).toBeInTheDocument());
    // Guidance copy: rules first, model may propose a custom label (LUM-631).
    expect(screen.getByText(/on-device model may also propose a category/i)).toBeInTheDocument();
  });

  it("PUTs a new custom category", async () => {
    const put = vi.fn();
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      if (u.includes("/email-taxonomy")) {
        if (init?.method === "PUT") {
          put(JSON.parse(String(init.body)));
          return jsonResponse(200, DEFAULTS);
        }
        return jsonResponse(200, DEFAULTS);
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;

    renderView(fetchImpl);
    await waitFor(() => expect(screen.getByLabelText("Reclassify School")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("New category slug"), { target: { value: "receipts" } });
    fireEvent.change(screen.getByLabelText("New category label"), { target: { value: "Receipts" } });
    fireEvent.click(screen.getByText("Add"));

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith({ categories: [{ slug: "receipts", label: "Receipts" }] }),
    );
  });

  it("PUTs a body-keyword rule (LUM-630)", async () => {
    const putRules = vi.fn();
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-classification-rules")) {
        if (init?.method === "PUT") {
          putRules(JSON.parse(String(init.body)));
          return jsonResponse(200, { rules: [] });
        }
        return jsonResponse(200, { rules: [] });
      }
      if (u.includes("/email-taxonomy")) return jsonResponse(200, DEFAULTS);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;

    renderView(fetchImpl);
    await waitFor(() => expect(screen.getByLabelText("Reclassify School")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Rule body keywords"), { target: { value: "field trip" } });
    fireEvent.change(screen.getByLabelText("Rule category"), { target: { value: "school" } });
    fireEvent.click(screen.getByText("Add rule"));

    await waitFor(() =>
      expect(putRules).toHaveBeenCalledWith({
        rules: [expect.objectContaining({ category: "school", match: { body_contains: ["field trip"] } })],
      }),
    );
  });

  it("calls reclassify for a category", async () => {
    const posted: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
      if (u.includes("/email-classification-rules")) return jsonResponse(200, { rules: [] });
      if (u.includes("/reclassify")) {
        posted.push(u);
        return jsonResponse(200, { slug: "school", scanned: 3, moved: 1, failed: 0 });
      }
      if (u.includes("/email-taxonomy")) return jsonResponse(200, DEFAULTS);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;

    renderView(fetchImpl);
    await waitFor(() => expect(screen.getByLabelText("Reclassify School")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Reclassify School"));
    await waitFor(() => expect(posted.some((u) => u.includes("/email-taxonomy/school/reclassify"))).toBe(true));
  });
});
