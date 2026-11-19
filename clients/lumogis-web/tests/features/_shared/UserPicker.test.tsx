// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { UserPicker } from "../../../src/features/_shared/UserPicker";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
const users = [
  { id: "a1", email: "admin@home.lan", role: "admin", disabled: false },
  { id: "u2", email: "bob@home.lan", role: "user", disabled: true },
];

describe("UserPicker", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    seedSignedInDevice("a1");
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mount(isAdmin: boolean, usersStatus = 200) {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      if (u.includes("/api/v1/admin/users")) return jsonResponse(usersStatus, usersStatus === 200 ? users : {});
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const onChange = vi.fn();
    function Harness() {
      const [v, setV] = useState("");
      return (
        <UserPicker
          value={v}
          isAdmin={isAdmin}
          onChange={(id) => {
            onChange(id);
            setV(id);
          }}
        />
      );
    }
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <Harness />
      </AuthProvider>,
    );
    return { onChange, fetchImpl };
  }

  it("lists people with plain role words and reports the chosen id", async () => {
    const { onChange } = mount(true);
    const userEv = userEvent.setup();
    const box = await screen.findByRole("combobox", { name: "User" });
    expect(box).toHaveTextContent("Choose a person");
    await userEv.click(box);
    expect(await screen.findByRole("option", { name: "admin@home.lan (Admin)" })).toBeInTheDocument();
    await userEv.click(screen.getByRole("option", { name: "bob@home.lan (Member, turned off)" }));
    expect(onChange).toHaveBeenCalledWith("u2");
    expect(screen.getByRole("combobox", { name: "User" })).toHaveTextContent("bob@home.lan (Member, turned off)");
  });

  it("says so for a non-admin and does not ask the server", () => {
    const { fetchImpl } = mount(false);
    expect(screen.getByText(/only an admin can pick a person/i)).toBeInTheDocument();
    expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("/admin/users"))).toBe(false);
  });

  it("shows a plain failure when the list cannot load", async () => {
    mount(true, 500);
    expect(await screen.findByText(/couldn't load the list of people/i)).toBeInTheDocument();
  });
});
