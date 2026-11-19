// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The navigation shell (LUM-993 Pass 2a): what the menu shows (Decision 16),
// the desktop sidebar with its account menu, and the phone tab bar with More.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = {
  user: { id: "u1", email: "anna.meier@home.lan", role: "admin" as "admin" | "user" },
  logout: vi.fn(async () => {}),
};
vi.mock("../../../src/auth/AuthProvider", () => ({
  useUser: () => auth.user,
  useAuth: () => ({ logout: auth.logout }),
}));

import { AppSidebar } from "../../../src/components/shell/AppSidebar";
import { MobileNav } from "../../../src/components/shell/MobileNav";
import { initialsFor } from "../../../src/components/shell/initials";
import { buildShellNav, pathToNavKey } from "../../../src/components/shell/navItems";
import { TooltipProvider } from "../../../src/components/ui/tooltip";

const labels = (items: ReadonlyArray<{ label: string }>) => items.map((i) => i.label);

describe("buildShellNav (what the menu shows)", () => {
  it("always shows the four core places; Email and Approvals only when they apply", () => {
    const bare = buildShellNav({ emailActive: false, canAct: false, isAdmin: false });
    expect(labels(bare.primary)).toEqual(["Chat", "Search", "Library", "Capture"]);
    expect(labels(bare.secondary)).toEqual(["Settings"]);
    // Only Settings would be in More, so Settings is the fifth tab and there is no More.
    expect(labels(bare.tabs)).toEqual(["Chat", "Search", "Library", "Capture", "Settings"]);
    expect(bare.more).toEqual([]);

    const full = buildShellNav({ emailActive: true, canAct: true, isAdmin: true, pendingApprovals: 3 });
    expect(labels(full.primary)).toEqual(["Chat", "Search", "Library", "Capture", "Email", "Approvals"]);
    // LUM-993 Pass 3: household pages live inside Settings; there is no Admin item.
    expect(labels(full.secondary)).toEqual(["Settings"]);
    expect(labels(full.tabs)).toEqual(["Chat", "Search", "Library", "Capture"]);
    expect(labels(full.more)).toEqual(["Email", "Approvals", "Settings"]);
    expect(full.pendingApprovals).toBe(3);
  });

  it("hides Approvals and its count while nothing can act", () => {
    const nav = buildShellNav({ emailActive: true, canAct: false, isAdmin: false, pendingApprovals: 2 });
    expect(labels(nav.primary)).not.toContain("Approvals");
    expect(labels(nav.more)).not.toContain("Approvals");
    expect(nav.pendingApprovals).toBeUndefined();
  });

  it("maps paths to the right place, including the Settings pages", () => {
    expect(pathToNavKey("/chat")).toBe("chat");
    expect(pathToNavKey("/entities/e1")).toBe("search");
    expect(pathToNavKey("/documents/d1/chat")).toBe("documents");
    expect(pathToNavKey("/email/attachments")).toBe("email");
    expect(pathToNavKey("/me/profile")).toBe("settings");
    expect(pathToNavKey("/audit")).toBe("settings");
    expect(pathToNavKey("/admin/users")).toBe("settings");
    expect(pathToNavKey("/settings/members")).toBe("settings");
    expect(pathToNavKey("/settingsx")).toBe("chat");
  });

  it("makes initials from the email", () => {
    expect(initialsFor("anna.meier@home.lan")).toBe("AM");
    expect(initialsFor("thomas@home.lan")).toBe("T");
    expect(initialsFor(undefined)).toBe("?");
  });
});

function sidebar(over: Partial<Parameters<typeof AppSidebar>[0]> = {}) {
  const nav = buildShellNav({ emailActive: true, canAct: true, isAdmin: true, pendingApprovals: 3 });
  const onNavigate = vi.fn();
  render(
    <TooltipProvider>
      <AppSidebar
        primary={nav.primary}
        secondary={nav.secondary}
        activeKey="chat"
        onNavigate={onNavigate}
        pendingApprovals={nav.pendingApprovals}
        {...over}
      />
    </TooltipProvider>,
  );
  return onNavigate;
}

describe("AppSidebar", () => {
  beforeEach(() => {
    auth.user = { id: "u1", email: "anna.meier@home.lan", role: "admin" };
    auth.logout.mockClear();
  });

  it("marks the current place and navigates inside the app", async () => {
    const onNavigate = sidebar({ activeKey: "documents" });
    const nav = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(nav).getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Chat" })).not.toHaveAttribute("aria-current");
    await userEvent.click(within(nav).getByRole("link", { name: "Search" }));
    expect(onNavigate).toHaveBeenCalledWith("/search");
  });

  it("leaves modified clicks (new tab, download) to the browser", async () => {
    const onNavigate = sidebar();
    const link = screen.getByRole("link", { name: "Search" });
    for (const key of ["{Control>}", "{Meta>}", "{Shift>}", "{Alt>}"]) {
      const user = userEvent.setup();
      await user.keyboard(key);
      await user.click(link);
      await user.keyboard(key.replace(">", "/").replace("{", "{/").replace("/}", "}"));
    }
    expect(onNavigate).not.toHaveBeenCalled();
    await userEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith("/search");
  });

  it("names the waiting count on Approvals", () => {
    sidebar();
    expect(screen.getByRole("link", { name: "Approvals, 3 waiting" })).toHaveAttribute("href", "/approvals");
  });

  it("collapses to icons with labels kept for screen readers, and remembers it on the page", async () => {
    sidebar();
    await userEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute("aria-expanded", "false");
    expect(document.documentElement.hasAttribute("data-sidebar-collapsed")).toBe(true);
    expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
    expect(screen.queryByText("Lumogis")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(document.documentElement.hasAttribute("data-sidebar-collapsed")).toBe(false);
  });

  it("opens the account menu with Profile, Look and feel and Sign out", async () => {
    const onNavigate = sidebar();
    await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(await screen.findByRole("menuitem", { name: "Profile" })).toBeInTheDocument();
    expect(screen.getByText("Household admin")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: "Look and feel" }));
    expect(onNavigate).toHaveBeenCalledWith("/settings/appearance");
    await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(auth.logout).toHaveBeenCalledTimes(1);
  });

  it("shows a member as Member and has no Admin place for them", async () => {
    auth.user = { id: "u2", email: "ben@home.lan", role: "user" };
    const nav = buildShellNav({ emailActive: false, canAct: false, isAdmin: false });
    sidebar({ primary: nav.primary, secondary: nav.secondary, pendingApprovals: undefined });
    expect(screen.queryByRole("link", { name: "Admin" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(await screen.findByText("Member")).toBeInTheDocument();
  });
});

describe("MobileNav", () => {
  beforeEach(() => {
    auth.user = { id: "u1", email: "anna.meier@home.lan", role: "admin" };
    auth.logout.mockClear();
  });

  function mobile(activeKey: Parameters<typeof MobileNav>[0]["activeKey"] = "chat") {
    const nav = buildShellNav({ emailActive: true, canAct: true, isAdmin: true, pendingApprovals: 2 });
    const onNavigate = vi.fn();
    render(<MobileNav tabs={nav.tabs} more={nav.more} activeKey={activeKey} onNavigate={onNavigate} pendingApprovals={nav.pendingApprovals} />);
    return onNavigate;
  }

  it("has four tabs and More", () => {
    mobile();
    const nav = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Chat", "Search", "Library", "Capture"]);
    expect(within(nav).getByRole("button", { name: "More, 2 waiting" })).toBeInTheDocument();
  });

  it("marks More as current for a page that lives in it", () => {
    mobile("settings");
    expect(screen.getByRole("button", { name: /^More/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Chat" })).not.toHaveAttribute("aria-current");
  });

  it("opens the More sheet with the other places and Sign out, and closes on a choice", async () => {
    const onNavigate = mobile();
    await userEvent.click(screen.getByRole("button", { name: /^More/ }));
    const sheet = await screen.findByRole("dialog", { name: "More" });
    expect(within(sheet).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/email", "/approvals", "/settings"]);
    expect(within(sheet).getByText("anna.meier@home.lan")).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("link", { name: /Approvals/ }));
    expect(onNavigate).toHaveBeenCalledWith("/approvals");
    await vi.waitFor(() => expect(screen.queryByRole("dialog", { name: "More" })).toBeNull());
  });

  it("shows Settings as the fifth tab, with no More, when nothing else would be in More", () => {
    const nav = buildShellNav({ emailActive: false, canAct: false, isAdmin: false });
    render(<MobileNav tabs={nav.tabs} more={nav.more} activeKey="settings" onNavigate={vi.fn()} />);
    const bar = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(bar).getAllByRole("link").map((a) => a.textContent)).toEqual(["Chat", "Search", "Library", "Capture", "Settings"]);
    expect(within(bar).getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    expect(within(bar).queryByRole("button", { name: /^More/ })).toBeNull();
  });

  it("signs out from the More sheet", async () => {
    mobile();
    await userEvent.click(screen.getByRole("button", { name: /^More/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(auth.logout).toHaveBeenCalledTimes(1);
  });
});
