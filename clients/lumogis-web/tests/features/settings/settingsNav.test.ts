// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The one list of Settings pages (LUM-993 Pass 3): every old address has
// exactly one new home, slugs are unique, every page has help text, and members
// never see household or developer pages.

import { describe, expect, it } from "vitest";

import { help } from "../../../src/copy/help";
import { mergeQuery } from "../../../src/features/settings/legacyQuery";
import {
  GROUPS,
  SETTINGS,
  isListed,
  legacyRedirectMap,
  listedGroups,
  mayOpen,
} from "../../../src/features/settings/settingsNav";

// The 34 old addresses the plan names: 16 /me/*, 15 /admin/*, /audit, /me, /admin.
const OLD_ME = [
  "profile", "appearance", "notifications", "email-signals", "email-labels", "relevance", "sources", "connectors",
  "llm-providers", "mcp-tokens", "permissions", "privacy-mode", "shared-items", "wont-learn", "export", "tools-capabilities",
].map((s) => `/me/${s}`);
const OLD_ADMIN = [
  "users", "connector-credentials", "system-mail", "connector-permissions", "mcp-tokens", "shared-items", "audit",
  "diagnostics", "system-status", "privacy-mode", "search-settings", "knowledge-graph", "secure-address",
  "feature-flags", "safety",
].map((s) => `/admin/${s}`);

describe("settingsNav", () => {
  it("covers exactly the 34 old addresses, each with one target", () => {
    const map = legacyRedirectMap();
    expect(Object.keys(map).sort()).toEqual([...OLD_ME, ...OLD_ADMIN, "/audit", "/me", "/admin"].sort());
    const slugs = new Set(SETTINGS.map((e) => e.slug));
    for (const to of Object.values(map)) {
      const slug = to.replace(/^\/settings\//, "").split("?")[0]!;
      expect(slugs.has(slug), to).toBe(true);
    }
  });

  it("sends old household addresses of Mine/Household pages to the Household view", () => {
    const map = legacyRedirectMap();
    expect(map["/admin/mcp-tokens"]).toBe("/settings/connected-apps?view=household");
    expect(map["/me/mcp-tokens"]).toBe("/settings/connected-apps");
    expect(map["/admin/audit"]).toBe("/settings/activity?view=household");
    expect(map["/audit"]).toBe("/settings/activity");
    expect(map["/admin/users"]).toBe("/settings/members");
    expect(map["/me"]).toBe("/settings/profile");
    expect(map["/admin"]).toBe("/settings/members");
  });

  it("keeps the old query string, with the target's own keys winning", () => {
    expect(mergeQuery("/settings/connected-apps", "?x=1")).toBe("/settings/connected-apps?x=1");
    expect(mergeQuery("/settings/activity?view=household", "?offset=40&x=1")).toBe("/settings/activity?offset=40&x=1&view=household");
    expect(mergeQuery("/settings/activity?view=household", "?view=mine")).toBe("/settings/activity?view=household");
    expect(mergeQuery("/settings/profile", "")).toBe("/settings/profile");
  });

  it("has unique slugs, a known group, and help text for every page", () => {
    const slugs = SETTINGS.map((e) => e.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    const groups = new Set(GROUPS.map((g) => g.id));
    for (const e of SETTINGS) {
      expect(groups.has(e.group), e.slug).toBe(true);
      const copy = help[e.helpKey] as { page?: string; about?: string };
      expect(copy.page, `${e.slug}: help.${e.helpKey}.page`).toBeTruthy();
      expect(copy.about, `${e.slug}: help.${e.helpKey}.about`).toBeTruthy();
      // Exactly one of a single view or Mine/Household views.
      expect(("Component" in e && e.Component !== undefined) !== (e.scope !== undefined), e.slug).toBe(true);
    }
  });

  it("shows members no household or developer pages, and admins developer pages only with the switch", () => {
    const member = { isAdmin: false, devTools: true };
    const admin = { isAdmin: true, devTools: false };
    const adminDev = { isAdmin: true, devTools: true };
    expect(SETTINGS.filter((e) => isListed(e, member)).every((e) => e.audience === "everyone")).toBe(true);
    expect(listedGroups(member).map((g) => g.group.id)).toEqual(["you", "email", "connections", "privacy"]);
    expect(listedGroups(admin).map((g) => g.group.id)).toEqual(["you", "email", "connections", "privacy", "household", "advanced"]);
    expect(listedGroups(adminDev).map((g) => g.group.id)).toContain("developers");
    // Opening by address: members are kept out; developer pages open for admins even with the switch off.
    const dev = SETTINGS.find((e) => e.slug === "diagnostics")!;
    expect(mayOpen(dev, member)).toBe(false);
    expect(mayOpen(dev, admin)).toBe(true);
    expect(mayOpen(SETTINGS.find((e) => e.slug === "members")!, member)).toBe(false);
    expect(mayOpen(SETTINGS.find((e) => e.slug === "connected-apps")!, member)).toBe(true);
  });
});
