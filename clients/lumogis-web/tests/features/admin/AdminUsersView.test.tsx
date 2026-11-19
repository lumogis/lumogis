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
import { seedSignedInDevice } from "../../helpers/signedInDevice";


// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("a1");
});

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
    const userEv = userEvent.setup();
    await userEv.click(within(row).getByRole("button", { name: /more actions/i }));
    const menu = await screen.findByRole("menu");
    // LUM-520: role `user` is surfaced as "Member" → the admin's demote item reads "Make Member".
    const makeBtn = within(menu).getByRole("menuitem", { name: /^make member$/i });
    const disBtn = within(menu).getByRole("menuitem", { name: /^turn off$/i });
    const delBtn = within(menu).getByRole("menuitem", { name: /^delete$/i });
    expect(makeBtn).toHaveAttribute("aria-disabled", "true");
    expect(disBtn).toHaveAttribute("aria-disabled", "true");
    expect(delBtn).toHaveAttribute("aria-disabled", "true");
    // The reason is plain text on the row (menu items carry no tooltip).
    expect(within(row).getByText(/only active admin/i)).toBeInTheDocument();
  });

  it("shows each member as a row with a role badge and a plain last-active line", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const users = [
      {
        id: "a1",
        email: "admin@home.lan",
        role: "admin" as const,
        disabled: false,
        created_at: "2020-01-01T00:00:00Z",
        last_login_at: null,
        last_seen_at: "2026-06-22T10:30:00Z",
      },
      {
        id: "t1",
        email: "bob@home.lan",
        role: "user" as const,
        disabled: true,
        created_at: "2020-01-01T00:00:00Z",
        last_login_at: null,
        last_seen_at: null,
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
    expect(screen.getByTestId("admin-users-table-scroll")).toContainElement(table);
    const adminRow = screen.getByRole("row", { name: /admin@home\.lan/i });
    expect(within(adminRow).getByText("Admin")).toBeInTheDocument();
    expect(within(adminRow).getByText(/last active 22 jun 2026/i)).toBeInTheDocument();
    const bobRow = screen.getByRole("row", { name: /bob@home\.lan/i });
    expect(within(bobRow).getByText("Member")).toBeInTheDocument();
    expect(within(bobRow).getByText("Turned off")).toBeInTheDocument();
    expect(within(bobRow).getByText("No activity yet")).toBeInTheDocument();
  });

  it("shows a retry state when members cannot load", async () => {
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users")) return jsonResponse(500, {});
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    expect(await screen.findByText(/couldn't load your members/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
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
    await userEv.click(within(row).getByRole("button", { name: /more actions/i }));
    await userEv.click(within(await screen.findByRole("menu")).getByRole("menuitem", { name: /^reset password$/i }));
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
    const userEv = userEvent.setup();
    await userEv.click(within(row).getByRole("button", { name: /more actions/i }));
    expect(within(await screen.findByRole("menu")).getByRole("menuitem", { name: /^export backup$/i })).toBeInTheDocument();
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
    await userEv.click(within(row).getByRole("button", { name: /more actions/i }));
    await userEv.click(within(await screen.findByRole("menu")).getByRole("menuitem", { name: /^export backup$/i }));
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
    await userEv.click(await screen.findByRole("combobox", { name: /^backup archive$/i }));
    await userEv.click(await screen.findByRole("option", { name: /orig@example\.com — export_1\.zip/ }));
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
    await screen.findByRole("combobox", { name: /^backup archive$/i });
    await userEv.type(screen.getByLabelText(/^new account email$/i), "new@example.com");
    await userEv.type(screen.getByLabelText(/^new account password/i), "newpassword1234");
    await userEv.click(screen.getByRole("button", { name: /^run preview$/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/select a backup archive/i);
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
    await userEv.click(await screen.findByRole("combobox", { name: /^backup archive$/i }));
    await userEv.click(await screen.findByRole("option", { name: /export_1\.zip/ }));
    await userEv.type(screen.getByLabelText(/^new account email$/i), "new@example.com");
    const pw = screen.getByLabelText(/^new account password/i);
    await userEv.type(pw, "newpassword1234");
    await userEv.click(screen.getByRole("checkbox", { name: /^preview only/i }));
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

  /** Open the "…" menu of `row` and return the menu. */
  async function openMenu(userEv: ReturnType<typeof userEvent.setup>, rowEl: HTMLElement) {
    await userEv.click(within(rowEl).getByRole("button", { name: /more actions/i }));
    return screen.findByRole("menu");
  }

  const patched = (fetchImpl: ReturnType<typeof mountWith>, id: string) =>
    fetchImpl.mock.calls.filter((c) => String(c[0]).includes(`/admin/users/${id}`) && c[1]?.method === "PATCH");

  it("promote: confirm accepted → PATCHes role admin", async () => {
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
    const menu = await openMenu(userEv, bobRow);
    await userEv.click(within(menu).getByRole("menuitem", { name: /^make admin$/i }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/make bob@home\.lan an admin\?/i)).toBeInTheDocument();
    expect(patched(fetchImpl, "t1")).toHaveLength(0);
    await userEv.click(within(dialog).getByRole("button", { name: /^make admin$/i }));
    await waitFor(() => expect(patched(fetchImpl, "t1")).toHaveLength(1));
  });

  it("demote: Cancel on the confirm → no PATCH fired", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    // Two admins so neither is the last active admin (Make item stays enabled).
    const fetchImpl = mountWith(admin, [
      row({ id: "a1", email: "admin@home.lan", role: "admin" }),
      row({ id: "a2", email: "carol@home.lan", role: "admin" }),
    ]);
    const userEv = userEvent.setup();
    const carolRow = await screen.findByRole("row", { name: /carol@home\.lan/i });
    const menu = await openMenu(userEv, carolRow);
    await userEv.click(within(menu).getByRole("menuitem", { name: /^make member$/i }));
    const dialog = await screen.findByRole("alertdialog");
    await userEv.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(patched(fetchImpl, "a2")).toHaveLength(0);
  });

  it("turn on: a turned-off member can be turned back on, without a confirm", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = mountWith(
      admin,
      [row({ id: "a1", email: "admin@home.lan", role: "admin" }), row({ id: "t1", email: "bob@home.lan", role: "user", disabled: true })],
      (u, init) => {
        if (u.includes("/api/v1/admin/users/t1") && init?.method === "PATCH") {
          expect(JSON.parse(String(init.body))).toEqual({ disabled: false });
          return jsonResponse(200, row({ id: "t1", email: "bob@home.lan", role: "user", disabled: false }));
        }
        return null;
      },
    );
    const userEv = userEvent.setup();
    const bobRow = await screen.findByRole("row", { name: /bob@home\.lan/i });
    const item = within(await openMenu(userEv, bobRow)).getByRole("menuitem", { name: /^turn on$/i });
    expect(item).not.toHaveAttribute("aria-disabled", "true");
    await userEv.click(item);
    await waitFor(() => expect(patched(fetchImpl, "t1")).toHaveLength(1));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("turn off: confirm says what happens; confirming PATCHes disabled true, cancelling does nothing", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = mountWith(
      admin,
      [row({ id: "a1", email: "admin@home.lan", role: "admin" }), row({ id: "t1", email: "bob@home.lan", role: "user" })],
      (u, init) => {
        if (u.includes("/api/v1/admin/users/t1") && init?.method === "PATCH") {
          expect(JSON.parse(String(init.body))).toEqual({ disabled: true });
          return jsonResponse(200, row({ id: "t1", email: "bob@home.lan", role: "user", disabled: true }));
        }
        return null;
      },
    );
    const userEv = userEvent.setup();
    const bobRow = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(await openMenu(userEv, bobRow)).getByRole("menuitem", { name: /^turn off$/i }));
    let dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/will not be able to use Lumogis/i);
    await userEv.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(patched(fetchImpl, "t1")).toHaveLength(0);

    await userEv.click(within(await openMenu(userEv, bobRow)).getByRole("menuitem", { name: /^turn off$/i }));
    dialog = await screen.findByRole("alertdialog");
    await userEv.click(within(dialog).getByRole("button", { name: /^turn off$/i }));
    await waitFor(() => expect(patched(fetchImpl, "t1")).toHaveLength(1));
  });

  it("delete: confirming sends DELETE", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = mountWith(
      admin,
      [row({ id: "a1", email: "admin@home.lan", role: "admin" }), row({ id: "t1", email: "bob@home.lan", role: "user" })],
      (u, init) => (u.includes("/api/v1/admin/users/t1") && init?.method === "DELETE" ? jsonResponse(200, {}) : null),
    );
    const userEv = userEvent.setup();
    const bobRow = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(await openMenu(userEv, bobRow)).getByRole("menuitem", { name: /^delete$/i }));
    const dialog = await screen.findByRole("alertdialog");
    await userEv.click(within(dialog).getByRole("button", { name: /^delete$/i }));
    await waitFor(() =>
      expect(
        fetchImpl.mock.calls.some((c) => String(c[0]).includes("/admin/users/t1") && c[1]?.method === "DELETE"),
      ).toBe(true),
    );
  });

  it("self row: Turn off and Delete are disabled and the reason is on the row", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    // Second active admin so a1 is NOT the last admin — the only reason to block is self.
    mountWith(admin, [
      row({ id: "a1", email: "admin@home.lan", role: "admin" }),
      row({ id: "a2", email: "carol@home.lan", role: "admin" }),
    ]);
    const selfRow = await screen.findByRole("row", { name: /admin@home\.lan/i });
    const userEv = userEvent.setup();
    expect(within(selfRow).getByText(/you can't turn off or delete your own account/i)).toBeInTheDocument();
    const menu = await openMenu(userEv, selfRow);
    expect(within(menu).getByRole("menuitem", { name: /^turn off$/i })).toHaveAttribute("aria-disabled", "true");
    expect(within(menu).getByRole("menuitem", { name: /^delete$/i })).toHaveAttribute("aria-disabled", "true");
    await userEv.keyboard("{Escape}");
    // A different admin row is not self-blocked.
    const otherRow = await screen.findByRole("row", { name: /carol@home\.lan/i });
    const otherMenu = await openMenu(userEv, otherRow);
    expect(within(otherMenu).getByRole("menuitem", { name: /^turn off$/i })).not.toHaveAttribute("aria-disabled", "true");
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

  it("keeps every action in one More menu per member row", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    mountWith(admin, [
      row({ id: "a1", email: "admin@home.lan", role: "admin" }),
      row({ id: "t1", email: "bob@home.lan", role: "user" }),
    ]);
    const bobRow = await screen.findByRole("row", { name: /bob@home\.lan/i });
    expect(within(bobRow).getAllByRole("button")).toHaveLength(1);
    const menu = await openMenu(userEvent.setup(), bobRow);
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "Set name",
      "Make Admin",
      "Reset password",
      "Export backup",
      "Turn off",
      "Delete",
    ]);
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

  function mountInvite(
    onMintBody: (body: unknown) => void,
    inviteUrl = "http://home.lan/invite?token=abc123def456ghi",
  ) {
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
          invite_url: inviteUrl,
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
    const toggle = screen.getByRole("checkbox", { name: /allow access to shared household memory/i });
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
    const toggle = screen.getByRole("checkbox", { name: /allow access to shared household memory/i });
    await userEv.click(toggle);
    expect(toggle).not.toBeChecked();
    // …then switch the role to Admin: the toggle is forced on and disabled.
    await userEv.click(screen.getByRole("combobox", { name: /role for new member/i }));
    await userEv.click(await screen.findByRole("option", { name: "Admin" }));
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAttribute("aria-disabled", "true");
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
    const row = screen.getByRole("row", { name: /bob@home\.lan/i });
    await ev.click(within(row).getByRole("button", { name: /more actions/i }));
    await ev.click(await screen.findByRole("menuitem", { name: /^set name$/i }));
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
    const row = screen.getByRole("row", { name: /bob@home\.lan/i });
    await ev.click(within(row).getByRole("button", { name: /more actions/i }));
    await ev.click(await screen.findByRole("menuitem", { name: /^set name$/i }));
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

describe("AdminUsersView — invite link is absolute (LUM-981)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mount(inviteUrl: string) {
    const me = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, me);
      if (u.includes("/api/v1/admin/users/invites") && init?.method === "POST") {
        return jsonResponse(201, {
          invite: {
            id: "i1",
            role: "user",
            allows_shared: true,
            created_by: "a1",
            created_at: "2020-01-01T00:00:00Z",
            expires_at: "2999-01-01T00:00:00Z",
            used_at: null,
            used_by: null,
            revoked_at: null,
            token_prefix: "abc123def456",
          },
          invite_url: inviteUrl,
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
  }

  it("A4 a relative invite_url from the API is shown and copied as an absolute link", async () => {
    mount("/invite?token=abc123def456ghi");
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: /^invite member$/i }));
    await userEv.click(screen.getByRole("button", { name: /generate invite link/i }));
    const expected = `${window.location.origin}/invite?token=abc123def456ghi`;
    const input = await screen.findByDisplayValue(expected);
    expect(input).toBeTruthy();
    await userEv.click(screen.getByRole("button", { name: /^copy link$/i }));
    expect(await navigator.clipboard.readText()).toBe(expected);
  });

  it("A4 an absolute invite_url (a pinned origin) is shown unchanged", async () => {
    mount("https://lumogis.example.com/invite?token=abc123def456ghi");
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: /^invite member$/i }));
    await userEv.click(screen.getByRole("button", { name: /generate invite link/i }));
    expect(await screen.findByDisplayValue("https://lumogis.example.com/invite?token=abc123def456ghi")).toBeTruthy();
  });

  it("shows the 'only works on this computer' hint when Lumogis is open at localhost", async () => {
    // jsdom's address is http://localhost, a loopback host.
    mount("/invite?token=abc123def456ghi");
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: /^invite member$/i }));
    await userEv.click(screen.getByRole("button", { name: /generate invite link/i }));
    expect(await screen.findByText(/This link only works on this computer/)).toBeTruthy();
  });
});

describe("AdminUsersView — invites list and create dialog (LUM-993 Pass 6)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    seedSignedInDevice("a1");
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const me = { id: "a1", email: "admin@home.lan", role: "admin" as const };
  const adminRow = {
    ...me,
    disabled: false,
    created_at: "2020-01-01T00:00:00Z",
    last_login_at: null,
    last_seen_at: null,
  };
  const invite = (id: string, over: Record<string, unknown> = {}) => ({
    id,
    role: "user",
    allows_shared: true,
    created_by: "a1",
    created_at: "2020-01-01T00:00:00Z",
    expires_at: "2999-01-01T00:00:00Z",
    used_at: null,
    used_by: null,
    revoked_at: null,
    token_prefix: "abc123def456",
    ...over,
  });

  function mount(invites: unknown[], extra?: (u: string, init?: RequestInit) => Response | null) {
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, me);
      const hit = extra?.(u, init);
      if (hit) return hit;
      if (u.includes("/api/v1/admin/users/invites")) return jsonResponse(200, { invites });
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users")) return jsonResponse(200, [adminRow]);
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

  it("with no invites shows an empty state with the next action", async () => {
    mount([]);
    expect(await screen.findByText("No invites yet")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /^invite someone$/i }));
    expect(await screen.findByRole("dialog", { name: /invite member/i })).toBeInTheDocument();
  });

  it("lists an active invite; Revoke asks first, Cancel does nothing, confirming sends DELETE", async () => {
    const fetchImpl = mount([invite("i1"), invite("i2", { used_at: "2020-02-01T00:00:00Z" })], (u, init) =>
      u.includes("/api/v1/admin/users/invites/i1") && init?.method === "DELETE" ? jsonResponse(200, {}) : null,
    );
    const userEv = userEvent.setup();
    expect(await screen.findByText("Redeemed")).toBeInTheDocument();
    // Only the active invite has a "…" menu.
    const triggers = await screen.findAllByRole("button", { name: /actions for member invite/i });
    expect(triggers).toHaveLength(1);
    const revoked = () =>
      fetchImpl.mock.calls.some((c) => String(c[0]).includes("/invites/i1") && c[1]?.method === "DELETE");

    await userEv.click(triggers[0]!);
    await userEv.click(await screen.findByRole("menuitem", { name: /^revoke$/i }));
    let dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/stops working before it is used/i);
    await userEv.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(revoked()).toBe(false);

    await userEv.click(triggers[0]!);
    await userEv.click(await screen.findByRole("menuitem", { name: /^revoke$/i }));
    dialog = await screen.findByRole("alertdialog");
    await userEv.click(within(dialog).getByRole("button", { name: /^revoke$/i }));
    await waitFor(() => expect(revoked()).toBe(true));
  });

  it("keeps the invite code prefix inside a collapsed Details", async () => {
    mount([invite("i1")]);
    const userEv = userEvent.setup();
    await screen.findByText(/member invite/i);
    expect(screen.queryByText("abc123def456")).toBeNull();
    await userEv.click(screen.getByRole("button", { name: "Details" }));
    expect(await screen.findByText("abc123def456")).toBeInTheDocument();
  });

  it("Create user dialog posts the new account and Cancel closes it", async () => {
    const bodies: unknown[] = [];
    mount([], (u, init) => {
      if (u.endsWith("/api/v1/admin/users") && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        return jsonResponse(201, { ...adminRow, id: "n1", email: "new@home.lan", role: "user" });
      }
      return null;
    });
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: /^create user$/i }));
    const dialog = await screen.findByRole("dialog", { name: /new user/i });
    await userEv.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEv.click(screen.getByRole("button", { name: /^create user$/i }));
    const again = await screen.findByRole("dialog", { name: /new user/i });
    await userEv.type(within(again).getByLabelText("Email"), "new@home.lan");
    await userEv.type(within(again).getByLabelText(/^password/i), "newpassword1234");
    await userEv.click(within(again).getByRole("button", { name: /^create$/i }));
    await waitFor(() => expect(bodies).toEqual([{ email: "new@home.lan", password: "newpassword1234", role: "user" }]));
  });

  it("a failed create shows the reason inside the dialog", async () => {
    mount([], (u, init) =>
      u.endsWith("/api/v1/admin/users") && init?.method === "POST"
        ? jsonResponse(409, { detail: "That email is already in use." })
        : null,
    );
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: /^create user$/i }));
    const dialog = await screen.findByRole("dialog", { name: /new user/i });
    await userEv.type(within(dialog).getByLabelText("Email"), "new@home.lan");
    await userEv.type(within(dialog).getByLabelText(/^password/i), "newpassword1234");
    await userEv.click(within(dialog).getByRole("button", { name: /^create$/i }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/already in use/i);
  });
});
