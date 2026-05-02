// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — MeExportView: 413 user-facing message + blob path revokes on success.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeExportView } from "../../../src/features/me/MeExportView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MeExportView", () => {
  let originalFetch: typeof fetch;
  let originalCreate: typeof URL.createObjectURL;
  let originalRevoke: typeof URL.revokeObjectURL;
  let revokeImpl: (s: string) => void;
  let createObjectURLSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    createObjectURLSpy = vi.fn(() => "blob:mock");
    revokeImpl = vi.fn();
    const hadCreate = typeof URL.createObjectURL === "function";
    const hadRevoke = typeof URL.revokeObjectURL === "function";
    originalCreate = hadCreate
      ? (URL.createObjectURL.bind(URL) as unknown as typeof URL.createObjectURL)
      : (undefined as unknown as typeof URL.createObjectURL);
    originalRevoke = hadRevoke
      ? (URL.revokeObjectURL.bind(URL) as (s: string) => void)
      : (undefined as unknown as typeof URL.revokeObjectURL);
    URL.createObjectURL = createObjectURLSpy as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeImpl as (s: string) => void;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    if (typeof originalCreate === "function") {
      URL.createObjectURL = originalCreate;
    } else {
      delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    }
    if (typeof originalRevoke === "function") {
      URL.revokeObjectURL = originalRevoke;
    } else {
      delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
    }
  });

  it("sets status when export returns 413 (too large)", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const inv = [{ name: "s", kind: "k", row_count: 0 }];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/data-inventory")) return jsonResponse(200, inv);
      if (u.includes("/me/export") && init?.method === "POST")
        return new Response(null, { status: 413 });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeExportView />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/s \(k\):/)).toBeInTheDocument();
    });
    await userEv.click(screen.getByRole("button", { name: /download zip/i }));
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/archive too large/i);
    });
    expect(revokeImpl).not.toHaveBeenCalled();
  });

  it("does not silently refresh/retry when export returns 401", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const inv = [{ name: "s", kind: "k", row_count: 0 }];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/data-inventory")) return jsonResponse(200, inv);
      if (u.includes("/me/export") && init?.method === "POST")
        return jsonResponse(401, { detail: "expired" });
      if (u.includes("/api/v1/auth/refresh")) {
        throw new Error("export must not refresh/retry");
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    store.set("access-token");
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeExportView />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/s \(k\):/)).toBeInTheDocument();
    });
    await userEv.click(screen.getByRole("button", { name: /download zip/i }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/session expired/i);
    });
    const refreshCalls = fetchImpl.mock.calls.filter((c) => String(c[0]).includes("/api/v1/auth/refresh"));
    expect(refreshCalls).toHaveLength(0);
  });

  it("revokes the object URL after starting download on successful export", async () => {
    const origClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      /* jsdom: avoid Not implemented: navigation */
    };
    try {
      const user = { id: "u1", email: "u@home.lan", role: "user" as const };
      const inv = [{ name: "s", kind: "k", row_count: 0 }];
      const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
        const u = String(input);
        if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
        if (u.includes("/data-inventory")) return jsonResponse(200, inv);
        if (u.includes("/me/export") && init?.method === "POST") {
          return new Response(new Blob([new Uint8Array([1, 2, 3])]), {
            status: 200,
            headers: { "Content-Disposition": 'attachment; filename="e.zip"' },
          });
        }
        return jsonResponse(404, {});
      });
      const store = new AccessTokenStore();
      const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
      const userEv = userEvent.setup();

      render(
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <MeExportView />
        </AuthProvider>,
      );
      await waitFor(() => {
        expect(screen.getByText(/s \(k\):/)).toBeInTheDocument();
      });
      await userEv.click(screen.getByRole("button", { name: /download zip/i }));
      await waitFor(() => {
        expect(revokeImpl).toHaveBeenCalledWith("blob:mock");
      });
      expect(createObjectURLSpy).toHaveBeenCalled();
      expect(screen.getByRole("status")).toHaveTextContent(/download started/i);
    } finally {
      HTMLAnchorElement.prototype.click = origClick;
    }
  });
});
