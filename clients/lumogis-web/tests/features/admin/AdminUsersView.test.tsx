// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — AdminUsersView: last active admin has disabled role/disable/delete actions.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminUsersView } from "../../../src/features/admin/AdminUsersView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("AdminUsersView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("disables demote, disable, and delete for the sole active admin", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const users = [
      {
        id: "a1",
        email: "admin@home.lan",
        role: "admin" as const,
        disabled: false,
        created_at: "2020-01-01T00:00:00Z",
        last_login_at: null,
      },
    ];
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, users);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    const row = await screen.findByRole("row", { name: /admin@home\.lan/i });
    const makeBtn = within(row).getByRole("button", { name: /^make user$/i });
    const disBtn = within(row).getByRole("button", { name: /^disable$/i });
    const delBtn = within(row).getByRole("button", { name: /^delete$/i });
    expect(makeBtn).toBeDisabled();
    expect(disBtn).toBeDisabled();
    expect(delBtn).toBeDisabled();
    expect(makeBtn).toHaveAttribute("title", "Cannot remove the last active admin.");
  });

  it("wraps the users table in lumogis-table-scroll (Phase 2C)", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const users = [
      {
        id: "a1",
        email: "admin@home.lan",
        role: "admin" as const,
        disabled: false,
        created_at: "2020-01-01T00:00:00Z",
        last_login_at: null,
      },
    ];
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, users);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );

    const table = await screen.findByRole("table");
    expect(table.closest(".lumogis-table-scroll")).toBeTruthy();
    expect(table).toHaveClass("lumogis-dense-table");
  });

  it("reset password posts to /api/v1/admin/users/{id}/password", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const target = {
      id: "t1",
      email: "bob@home.lan",
      role: "user" as const,
      disabled: false,
      created_at: "2020-01-01T00:00:00Z",
      last_login_at: null,
    };
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, [user, target]);
      if (u.includes("/api/v1/admin/users/t1/password") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({ new_password: "newpassword1234" });
        return jsonResponse(200, { ok: true });
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    const row = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(row).getByRole("button", { name: /^reset password$/i }));
    await userEv.type(screen.getByLabelText(/^new password/i), "newpassword1234");
    await userEv.type(screen.getByLabelText(/^confirm password/i), "newpassword1234");
    await userEv.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(
        fetchImpl.mock.calls.some(
          (c) => String(c[0]).includes("/admin/users/t1/password") && c[1]?.method === "POST",
        ),
      ).toBe(true);
    });
  });

  it("shows Import from backup and per-row Export backup", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const target = {
      id: "t1",
      email: "bob@home.lan",
      role: "user" as const,
      disabled: false,
      created_at: "2020-01-01T00:00:00Z",
      last_login_at: null,
    };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, [user, target]);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    await screen.findByRole("button", { name: /^import from backup$/i });
    const row = await screen.findByRole("row", { name: /bob@home\.lan/i });
    expect(within(row).getByRole("button", { name: /^export backup$/i })).toBeInTheDocument();
  });

  it("export backup posts to /api/v1/me/export with target_user_id", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const target = {
      id: "t1",
      email: "bob@home.lan",
      role: "user" as const,
      disabled: false,
      created_at: "2020-01-01T00:00:00Z",
      last_login_at: null,
    };
    const zip = new Uint8Array([80, 75, 3, 4]);
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, [admin, target]);
      if (u.includes("/api/v1/me/export") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({ target_user_id: "t1" });
        return new Response(zip, {
          status: 200,
          headers: { "Content-Disposition": 'attachment; filename="export_t1.zip"' },
        });
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    const row = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(row).getByRole("button", { name: /^export backup$/i }));
    await waitFor(() => {
      expect(
        fetchImpl.mock.calls.some(
          (c) => String(c[0]).includes("/api/v1/me/export") && c[1]?.method === "POST",
        ),
      ).toBe(true);
    });
  });

  const minimalImportPlan = {
    manifest_version: 1,
    scope_filter: "authored_by_me",
    falkordb_edge_policy: "personal_intra_user_authored",
    exported_user: { email: "orig@example.com", role: "user" },
    sections: [],
    missing_sections: [],
    dangling_references: [],
    falkordb_external_edge_count: 0,
    preconditions: {
      archive_integrity_ok: true,
      manifest_present: true,
      manifest_parses: true,
      manifest_version_supported: true,
      target_email_available: true,
      all_required_sections_present: true,
      no_parent_pk_collisions: true,
    },
    would_succeed: true,
    warnings: [],
  };

  it("import preview posts dry_run true to /api/v1/admin/user-imports", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const other = {
      id: "t1",
      email: "bob@home.lan",
      role: "user" as const,
      disabled: false,
      created_at: "2020-01-01T00:00:00Z",
      last_login_at: null,
    };
    const inv = [
      {
        user_id: "t1",
        archive_filename: "export_1.zip",
        bytes: 100,
        mtime: "2020-01-01T00:00:00Z",
        manifest_status: "valid" as const,
        manifest_version: 1,
        exported_user_email: "orig@example.com",
      },
    ];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/user-imports") && init?.method === "GET") return jsonResponse(200, inv);
      if (u.includes("/api/v1/admin/user-imports") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          archive_path: "t1/export_1.zip",
          dry_run: true,
          new_user: { email: "new@example.com", password: "newpassword1234", role: "user" },
        });
        return jsonResponse(200, minimalImportPlan);
      }
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, [user, other]);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    await userEv.click(await screen.findByRole("button", { name: /^import from backup$/i }));
    await screen.findByLabelText(/^backup archive$/i);
    await userEv.selectOptions(screen.getByLabelText(/^backup archive$/i), "0");
    await userEv.type(screen.getByLabelText(/^new account email$/i), "new@example.com");
    await userEv.type(screen.getByLabelText(/^new account password/i), "newpassword1234");
    await userEv.click(screen.getByRole("button", { name: /^run preview$/i }));
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /import preview/i })).toBeInTheDocument();
    });
    expect(screen.queryByText(/password_hash/i)).toBeNull();
  });

  it("import shows validation error when archive not selected", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/user-imports") && init?.method === "GET") {
        return jsonResponse(200, [
          {
            user_id: "t1",
            archive_filename: "export_1.zip",
            bytes: 100,
            mtime: "2020-01-01T00:00:00Z",
            manifest_status: "valid" as const,
            manifest_version: 1,
            exported_user_email: null,
          },
        ]);
      }
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, [user]);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    await userEv.click(await screen.findByRole("button", { name: /^import from backup$/i }));
    await screen.findByLabelText(/^backup archive$/i);
    await userEv.type(screen.getByLabelText(/^new account email$/i), "new@example.com");
    await userEv.type(screen.getByLabelText(/^new account password/i), "newpassword1234");
    await userEv.click(screen.getByRole("button", { name: /^run preview$/i }));
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toMatch(/select a backup archive/i);
    });
    expect(
      fetchImpl.mock.calls.some((c) => String(c[0]).includes("/admin/user-imports") && c[1]?.method === "POST"),
    ).toBe(false);
  });

  it("import clears password field after successful non-dry run", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const other = {
      id: "t1",
      email: "bob@home.lan",
      role: "user" as const,
      disabled: false,
      created_at: "2020-01-01T00:00:00Z",
      last_login_at: null,
    };
    const inv = [
      {
        user_id: "t1",
        archive_filename: "export_1.zip",
        bytes: 100,
        mtime: "2020-01-01T00:00:00Z",
        manifest_status: "valid" as const,
        manifest_version: 1,
        exported_user_email: null,
      },
    ];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/user-imports") && init?.method === "GET") return jsonResponse(200, inv);
      if (u.includes("/api/v1/admin/user-imports") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            new_user_id: "n1",
            archive_filename: "export_1.zip",
            sections_imported: [],
            warnings: [],
          }),
          {
            status: 201,
            headers: { "Content-Type": "application/json", Location: "/api/v1/admin/users/n1" },
          },
        );
      }
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, [user, other]);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    await userEv.click(await screen.findByRole("button", { name: /^import from backup$/i }));
    await screen.findByLabelText(/^backup archive$/i);
    await userEv.selectOptions(screen.getByLabelText(/^backup archive$/i), "0");
    await userEv.type(screen.getByLabelText(/^new account email$/i), "new@example.com");
    const pw = screen.getByLabelText(/^new account password/i);
    await userEv.type(pw, "newpassword1234");
    await userEv.click(screen.getByLabelText(/^preview only/i));
    await userEv.click(screen.getByRole("button", { name: /^run import$/i }));
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /import result/i })).toBeInTheDocument();
    });
    expect((pw as HTMLInputElement).value).toBe("");
  });
});
