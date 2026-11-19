// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// One Settings (LUM-993 Pass 3) through real routes: the menu groups and their
// order, members kept out of household pages, the Household view only for
// admins, old addresses landing on their new page, developer pages, and one h1
// per page. The page views are stubbed: this tests the frame, not each view.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = { user: { id: "u1", email: "anna@home.lan", role: "admin" as "admin" | "user" } as { id: string; email: string; role: "admin" | "user" } | null };
// One stable auth value, as the real provider gives (a new object per call re-runs effects).
const authValue = { logout: vi.fn(async () => {}) };
vi.mock("../../../src/auth/AuthProvider", () => ({ useUser: () => auth.user, useAuth: () => authValue }));
const toast = vi.fn();
vi.mock("sonner", () => ({ toast: (m: string) => toast(m) }));

// Each view renders its name, so the test can see which one is on screen.
vi.mock("../../../src/features/settings/settingsNav", async (orig) => {
  const real = await orig<typeof import("../../../src/features/settings/settingsNav")>();
  const stub = (name: string) => () => <p data-testid="view">{name}</p>;
  const SETTINGS = real.SETTINGS.map((e) =>
    e.scope
      ? { ...e, scope: { mine: stub(`${e.slug}:mine`), household: stub(`${e.slug}:household`) } }
      : { ...e, Component: stub(e.slug) },
  ) as typeof real.SETTINGS;
  // The module's helpers read its own list, so they are rebuilt on the stubbed one.
  return {
    ...real,
    SETTINGS,
    entryBySlug: (slug: string | undefined) => SETTINGS.find((e) => e.slug === slug),
    listedGroups: (viewer: Parameters<typeof real.listedGroups>[0]) =>
      real.GROUPS.map((group) => ({
        group,
        entries: SETTINGS.filter((e) => e.group === group.id && real.isListed(e, viewer)),
      })).filter((g) => g.entries.length > 0),
  };
});

import { LegacyRedirect } from "../../../src/features/settings/LegacyRedirect";
import { SettingsIndex } from "../../../src/features/settings/SettingsHome";
import { SettingsLayout } from "../../../src/features/settings/SettingsLayout";
import { SettingsPage } from "../../../src/features/settings/SettingsPage";
import { ADMIN_ONLY_TOAST, HOUSEHOLD_VIEW_TOAST } from "../../../src/features/settings/settingsHeading";
import { legacyRedirectMap } from "../../../src/features/settings/settingsNav";

function Where(): JSX.Element {
  const loc = useLocation();
  return <output data-testid="where">{loc.pathname + loc.search}</output>;
}

function open(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<SettingsIndex />} />
          <Route path=":slug" element={<SettingsPage />} />
        </Route>
        {Object.entries(legacyRedirectMap()).map(([from, to]) => (
          <Route key={from} path={from} element={<LegacyRedirect to={to} />} />
        ))}
        <Route path="/me/*" element={<LegacyRedirect to="/settings/profile" />} />
        <Route path="/admin/*" element={<LegacyRedirect to="/settings/members" />} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

const view = () => screen.getByTestId("view").textContent;
const where = () => screen.getByTestId("where").textContent;

describe("Settings frame", () => {
  beforeEach(() => {
    auth.user = { id: "u1", email: "anna@home.lan", role: "admin" };
    toast.mockReset();
    localStorage.clear();
  });

  it("lists the groups in order; Advanced starts closed; For developers needs the switch", async () => {
    open("/settings/profile");
    const menu = screen.getByRole("navigation", { name: "Settings" });
    const headings = within(menu).getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["You", "Email and documents", "Connections", "Privacy and control", "Household"]);
    expect(within(menu).getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "false");
    expect(within(menu).queryByRole("link", { name: "Diagnostics" })).toBeNull();
    await userEvent.click(within(menu).getByRole("switch"));
    expect(await within(menu).findByRole("link", { name: "Diagnostics" })).toBeInTheDocument();
    expect(localStorage.getItem("lumogis-dev-tools")).toBe("1");
  });

  it("shows one h1 per page with the purpose line and its (i)", () => {
    open("/settings/members");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Members and invites");
    expect(screen.getByText("Everyone in the household, and invitations waiting to be used.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "About Members and invites" })).toBeInTheDocument();
    expect(view()).toBe("members");
  });

  it("opens the index at /settings with every listed page and its purpose", () => {
    open("/settings");
    const home = screen.getByTestId("settings-home");
    expect(within(home).getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(within(home).getByRole("link", { name: /Members and invites/ })).toHaveAttribute("href", "/settings/members");
    expect(within(home).queryByRole("link", { name: /Diagnostics/ })).toBeNull();
  });

  it("sends an unknown page to Profile without a toast", () => {
    open("/settings/whatever");
    expect(where()).toBe("/settings/profile");
    expect(toast).not.toHaveBeenCalled();
  });

  it("switches between Mine and Household for an admin, dropping paging", async () => {
    open("/settings/activity?preset=7d&offset=40");
    expect(view()).toBe("activity:mine");
    await userEvent.click(screen.getByRole("button", { name: "Household" }));
    expect(view()).toBe("activity:household");
    expect(where()).toBe("/settings/activity?preset=7d&view=household");
  });

  it("opens a developer page from a link with the switch off, with a notice", async () => {
    open("/settings/diagnostics");
    expect(view()).toBe("diagnostics");
    expect(screen.getByText("Developer tools are hidden in the menu.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show them" }));
    expect(screen.queryByText("Developer tools are hidden in the menu.")).toBeNull();
  });

  it("offers the developer switch on the index too, so it is reachable on a phone", () => {
    open("/settings");
    expect(within(screen.getByTestId("settings-home")).getByRole("switch", { name: "Show developer tools" })).toBeInTheDocument();
  });

  it("ends the index with Sign out, since a phone may have no More sheet", async () => {
    authValue.logout.mockClear();
    open("/settings");
    const home = screen.getByTestId("settings-home");
    expect(within(home).getByText("anna@home.lan")).toBeInTheDocument();
    await userEvent.click(within(home).getByRole("button", { name: "Sign out" }));
    expect(authValue.logout).toHaveBeenCalledTimes(1);
  });

  it("opens Advanced when you reach one of its pages from the index", async () => {
    open("/settings");
    const menu = screen.getByRole("navigation", { name: "Settings" });
    expect(within(menu).getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(within(screen.getByTestId("settings-home")).getByRole("link", { name: /Knowledge graph/ }));
    expect(within(menu).getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "true");
    expect(within(menu).getByRole("link", { name: "Knowledge graph" })).toHaveAttribute("aria-current", "page");
  });

  describe("old addresses", () => {
    it("an old personal address never opens the household view", () => {
      open("/me/privacy-mode?view=household&x=1");
      expect(where()).toBe("/settings/privacy?x=1");
      expect(view()).toBe("privacy:mine");
    });

    it("an unknown old address keeps its query, like the known ones", () => {
      open("/me/nothing-here?x=1");
      expect(where()).toBe("/settings/profile?x=1");
    });

    it.each([
      ["/me/profile", "/settings/profile", "profile"],
      ["/admin/users", "/settings/members", "members"],
      ["/admin/mcp-tokens", "/settings/connected-apps?view=household", "connected-apps:household"],
      ["/me/mcp-tokens?x=1", "/settings/connected-apps?x=1", "connected-apps:mine"],
      ["/audit", "/settings/activity", "activity:mine"],
      ["/me", "/settings/profile", "profile"],
      ["/admin", "/settings/members", "members"],
    ])("%s opens %s", (from, to, shown) => {
      open(from);
      expect(where()).toBe(to);
      expect(view()).toBe(shown);
    });
  });

  describe("members", () => {
    beforeEach(() => {
      auth.user = { id: "u2", email: "ben@home.lan", role: "user" };
    });

    it("see no household or developer groups and no developer switch", () => {
      open("/settings/profile");
      const menu = screen.getByRole("navigation", { name: "Settings" });
      expect(within(menu).getAllByRole("heading").map((h) => h.textContent)).toEqual([
        "You",
        "Email and documents",
        "Connections",
        "Privacy and control",
      ]);
      expect(within(menu).queryByRole("switch")).toBeNull();
    });

    it.each(["/settings/members", "/settings/diagnostics", "/settings/knowledge-graph"])(
      "are sent from %s to Profile with the admin-only message",
      (path) => {
        open(path);
        expect(where()).toBe("/settings/profile");
        expect(view()).toBe("profile");
      },
    );

    it("get Mine on a scoped page, never a switch, and a toast if they asked for Household", () => {
      open("/settings/connected-apps?view=household");
      expect(view()).toBe("connected-apps:mine");
      expect(screen.queryByRole("button", { name: "Household" })).toBeNull();
      expect(toast).toHaveBeenCalledWith(HOUSEHOLD_VIEW_TOAST);
      expect(where()).toBe("/settings/connected-apps");
    });

    it("following an old household bookmark land on the Mine view, not Profile", () => {
      open("/admin/mcp-tokens");
      expect(view()).toBe("connected-apps:mine");
    });
  });

  it("names the admin-only message as the plan says", () => {
    expect(ADMIN_ONLY_TOAST).toBe("Only household admins can open that page.");
  });
});
