// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Pass 6 — the household "What Lumogis may do" view: one row per changed setting,
// a plain Select, Save, and "Back to the default" in the row menu.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminConnectorPermissionsView } from "../../../src/features/admin/AdminConnectorPermissionsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

beforeEach(() => {
  seedSignedInDevice("admin");
});

const ADMIN = { id: "admin", email: "admin@home.lan", role: "admin" as const };
const ROW = {
  user_id: "u2",
  email: "bob@home.lan",
  connector: "smtp",
  mode: "ASK",
  is_default: false,
  updated_at: "2026-01-01T00:00:00Z",
};

function mount(rows: unknown[], listStatus = 200) {
  const puts: unknown[] = [];
  const deletes: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/api/v1/auth/me")) return jsonResponse(200, ADMIN);
    if (u.includes("/permissions/smtp") && init?.method === "PUT") {
      puts.push(JSON.parse(String(init.body)));
      return jsonResponse(200, { ...ROW, mode: "DO" });
    }
    if (u.includes("/permissions/smtp") && init?.method === "DELETE") {
      deletes.push(u);
      return jsonResponse(200, ROW);
    }
    if (u.includes("/api/v1/admin/permissions")) {
      return listStatus === 200 ? jsonResponse(200, rows) : jsonResponse(listStatus, { detail: "boom" });
    }
    return jsonResponse(404, { detail: "nope" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  render(
    <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
      <AdminConnectorPermissionsView />
    </AuthProvider>,
  );
  return { puts, deletes };
}

describe("AdminConnectorPermissionsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows the connector in plain words, never the raw id or mode", async () => {
    mount([ROW]);
    expect(await screen.findByText("bob@home.lan")).toBeInTheDocument();
    expect(screen.getByText("Send email as you")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bsmtp\b|\bASK\b/);
  });

  it("changes a setting and saves it", async () => {
    const { puts } = mount([ROW]);
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole("combobox", { name: /Setting for bob@home.lan/ }));
    await userEvent.click(await screen.findByRole("option", { name: "Just send" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts).toEqual([{ mode: "DO" }]));
  });

  it("puts a setting back to the default from the row menu", async () => {
    const { deletes } = mount([ROW]);
    await userEvent.click(await screen.findByRole("button", { name: /More actions for bob@home.lan/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Back to the default" }));
    await waitFor(() => expect(deletes).toHaveLength(1));
  });

  it("shows an empty state when nothing was changed", async () => {
    mount([]);
    expect(await screen.findByText("Nothing changed yet")).toBeInTheDocument();
  });

  it("offers a retry when loading fails", async () => {
    mount([], 500);
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
