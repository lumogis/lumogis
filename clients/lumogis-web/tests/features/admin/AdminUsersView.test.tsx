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
import { formatLastActive, roleLabel } from "../../../src/features/admin/adminUsersDisplay";
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
    // LUM-520: role `user` is surfaced as "Member" → the admin's demote button reads "Make Member".
    const makeBtn = within(row).getByRole("button", { name: /^make member$/i });
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

describe("AdminUsersView — LUM-520 finish/polish", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const row = (over: Partial<Record<string, unknown>> & { id: string; email: string; role: "admin" | "user" }) => ({
    disabled: false,
    created_at: "2020-01-01T00:00:00Z",
    last_login_at: null,
    last_seen_at: null,
    ...over,
  });

  function mountWith(me: { id: string; email: string; role: "admin" | "user" }, users: unknown[], extra?: (u: string, init?: RequestInit) => Response | null) {
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, me);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, users);
      const hit = extra?.(u, init);
      if (hit) return hit;
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    return fetchImpl;
  }

  it("promote: confirm accepted → PATCHes role admin", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = mountWith(
      admin,
      [row({ id: "a1", email: "admin@home.lan", role: "admin" }), row({ id: "t1", email: "bob@home.lan", role: "user" })],
      (u, init) => {
        if (u.includes("/api/v1/admin/users/t1") && init?.method === "PATCH") {
          expect(JSON.parse(String(init.body))).toEqual({ role: "admin" });
          return jsonResponse(200, { id: "t1", email: "bob@home.lan", role: "admin", disabled: false, created_at: "2020-01-01T00:00:00Z", last_login_at: null, last_seen_at: null });
        }
        return null;
      },
    );
    const userEv = userEvent.setup();
    const bobRow = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(bobRow).getByRole("button", { name: /^make admin$/i }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(
        fetchImpl.mock.calls.some((c) => String(c[0]).includes("/admin/users/t1") && c[1]?.method === "PATCH"),
      ).toBe(true);
    });
  });

  it("demote: confirm cancelled → no PATCH fired", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    // Two admins so neither is the last active admin (Make button stays enabled).
    const fetchImpl = mountWith(admin, [
      row({ id: "a1", email: "admin@home.lan", role: "admin" }),
      row({ id: "a2", email: "carol@home.lan", role: "admin" }),
    ]);
    const userEv = userEvent.setup();
    const carolRow = await screen.findByRole("row", { name: /carol@home\.lan/i });
    await userEv.click(within(carolRow).getByRole("button", { name: /^make member$/i }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(
      fetchImpl.mock.calls.some((c) => String(c[0]).includes("/admin/users/a2") && c[1]?.method === "PATCH"),
    ).toBe(false);
  });

  it("self row: Disable and Delete are disabled with friendly titles", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    // Second active admin so a1 is NOT the last admin — the only reason to block is self.
    mountWith(admin, [
      row({ id: "a1", email: "admin@home.lan", role: "admin" }),
      row({ id: "a2", email: "carol@home.lan", role: "admin" }),
    ]);
    const selfRow = await screen.findByRole("row", { name: /admin@home\.lan/i });
    const disableBtn = within(selfRow).getByRole("button", { name: /^disable$/i });
    const deleteBtn = within(selfRow).getByRole("button", { name: /^delete$/i });
    expect(disableBtn).toBeDisabled();
    expect(deleteBtn).toBeDisabled();
    expect(disableBtn).toHaveAttribute("title", "You can't disable your own account — ask another admin.");
    expect(deleteBtn).toHaveAttribute("title", "You can't delete your own account — ask another admin.");
    // A different admin row is not self-blocked.
    const otherRow = await screen.findByRole("row", { name: /carol@home\.lan/i });
    expect(within(otherRow).getByRole("button", { name: /^disable$/i })).not.toBeDisabled();
  });

  it("renders a member-count summary (singular/plural)", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    mountWith(admin, [
      row({ id: "a1", email: "admin@home.lan", role: "admin" }),
      row({ id: "t1", email: "bob@home.lan", role: "user" }),
      row({ id: "t2", email: "dee@home.lan", role: "user" }),
    ]);
    expect(await screen.findByText("2 members · 1 admin")).toBeInTheDocument();
  });
});

describe("AdminUsersView — invite shared-access toggle (LUM-577)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mountInvite(onMintBody: (body: unknown) => void) {
    const me = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, me);
      if (u.includes("/api/v1/admin/users/invites") && init?.method === "POST") {
        onMintBody(JSON.parse(String(init.body)));
        return jsonResponse(201, {
          invite: {
            id: "i1",
            role: "user",
            allows_shared: false,
            created_by: "a1",
            created_at: "2020-01-01T00:00:00Z",
            expires_at: "2999-01-01T00:00:00Z",
            used_at: null,
            used_by: null,
            revoked_at: null,
            token_prefix: "abc123def456",
          },
          invite_url: "http://home.lan/invite?token=abc123def456ghi",
          token: "abc123def456ghi",
        });
      }
      if (u.includes("/api/v1/admin/users/invites")) return jsonResponse(200, { invites: [] });
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, [me]);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    return fetchImpl;
  }

  it("member invite posts allows_shared=false when the shared-access toggle is unchecked", async () => {
    let body: unknown = null;
    mountInvite((b) => {
      body = b;
    });
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: /^invite member$/i }));
    // Default is checked (allows shared) — uncheck it to withhold shared-scope access.
    const toggle = screen.getByLabelText(/allow access to shared household memory/i);
    expect(toggle).toBeChecked();
    await userEv.click(toggle);
    await userEv.click(screen.getByRole("button", { name: /generate invite link/i }));
    await waitFor(() => {
      expect(body).toEqual({ role: "user", allows_shared: false });
    });
  });

  it("admin role forces shared access on (toggle disabled) and posts allows_shared=true", async () => {
    let body: unknown = null;
    mountInvite((b) => {
      body = b;
    });
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: /^invite member$/i }));
    // Withhold shared access as a member first…
    const toggle = screen.getByLabelText(/allow access to shared household memory/i);
    await userEv.click(toggle);
    expect(toggle).not.toBeChecked();
    // …then switch the role to Admin: the toggle is forced on and disabled.
    await userEv.selectOptions(screen.getByLabelText(/role for new member/i), "admin");
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    await userEv.click(screen.getByRole("button", { name: /generate invite link/i }));
    await waitFor(() => {
      expect(body).toEqual({ role: "admin", allows_shared: true });
    });
  });
});

describe("AdminUsersView display name (LUM-585)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function setup() {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const users = [
      { id: "a1", email: "admin@home.lan", role: "admin", disabled: false, created_at: "2020-01-01T00:00:00Z", last_login_at: null, last_seen_at: null, display_name: null },
      { id: "u2", email: "bob@home.lan", role: "user", disabled: false, created_at: "2020-01-02T00:00:00Z", last_login_at: null, last_seen_at: null, display_name: null },
    ];
    const patchBodies: unknown[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      if (u.includes("/api/v1/admin/users/u2") && init?.method === "PATCH") {
        patchBodies.push(JSON.parse(String(init.body)));
        return jsonResponse(200, { ...users[1], display_name: "Alex" });
      }
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, users);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    return { client, store, patchBodies };
  }

  it("sets a display name via the Set name modal → PATCH display_name", async () => {
    const { client, store, patchBodies } = setup();
    const ev = userEvent.setup();
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    await screen.findByText("bob@home.lan");
    await ev.click(screen.getByTestId("set-name-u2"));
    const input = await screen.findByTestId("display-name-input");
    await ev.type(input, "Alex");
    await ev.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(patchBodies).toHaveLength(1));
    expect(patchBodies[0]).toEqual({ display_name: "Alex" });
  });

  it("clearing the field sends an empty display_name (→ NULL fallback)", async () => {
    const { client, store, patchBodies } = setup();
    const ev = userEvent.setup();
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    await screen.findByText("bob@home.lan");
    await ev.click(screen.getByTestId("set-name-u2"));
    await screen.findByTestId("display-name-input");
    // Field starts empty (display_name was null) → save clears it.
    await ev.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(patchBodies).toHaveLength(1));
    expect(patchBodies[0]).toEqual({ display_name: "" });
  });
});

describe("AdminUsersView display helpers (LUM-334/520)", () => {
  describe("roleLabel", () => {
    it("shows admin as 'Admin' and the wire role 'user' as 'Member'", () => {
      expect(roleLabel("admin")).toBe("Admin");
      expect(roleLabel("user")).toBe("Member");
    });
    it("passes an unknown/future role through verbatim (not mislabelled 'Member')", () => {
      expect(roleLabel("guest")).toBe("guest");
    });
  });

  describe("formatLastActive", () => {
    it("renders 'never' for null/undefined/empty (no last_seen_at yet)", () => {
      expect(formatLastActive(null)).toBe("never");
      expect(formatLastActive(undefined)).toBe("never");
      expect(formatLastActive("")).toBe("never");
    });
    it("renders '—' for an unparseable value", () => {
      expect(formatLastActive("not-a-date")).toBe("—");
    });
    it("renders a non-empty localised string for a valid ISO timestamp", () => {
      const out = formatLastActive("2026-06-22T10:30:00Z");
      expect(out).not.toBe("never");
      expect(out).not.toBe("—");
      expect(out.length).toBeGreaterThan(0);
    });
  });
});
