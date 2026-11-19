// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-161 — "What Lumogis knows": Search → Browse → list → entry page with
// "Mentioned in" → owner renames. Desktop and mobile viewports.
// Requires live stack + smoke credentials (same contract as documents.spec.ts).
//
// Hybrid pattern (as entity-sharing.spec.ts): real smoke login + stateful
// `page.route` mocks for the /api/v1/kg routes.
//
// What this spec does NOT prove, on purpose (vacuous-test principle): which
// entries or mentions a member may see. That is computed by the server and is
// proven against real Postgres in
// orchestrator/tests/integration/test_entity_browser_live.py (two members,
// shared vs personal, allows_shared=false). A mock here would only assert the
// mock. This spec covers the user-visible flow: navigation, nav highlight,
// zero state, counts, the mention link, rename, the kind correction, and the
// missing Edit / "This is wrong…" controls on another member's entry.
//
// LUM-967: the remembered correction (a re-read cannot bring the old kind back),
// the 409 collision rule and two-member isolation are proven against real
// Postgres in orchestrator/tests/integration/test_entity_kind_live.py, and the
// in-place graph relabel in test_graph_entity_relabel_falkordb_live.py. Here the
// 409 is a mock answer: it proves the dialog's wording and link, not the rule.
//
// LUM-965 (merge, History, undo, move mentions): ownership, isolation, that the
// undo record restores the other entry exactly (rows, ids, names, shared copy),
// newest-first undo, "not the same again" and the graph following undo are all
// computed on the server and proven in
// orchestrator/tests/integration/test_entity_merge_undo_live.py and
// test_graph_merge_undo_falkordb_live.py (vacuous-test principle). The mocks here
// only drive the flow: menu → pick → confirm → History → Undo → mover.
//
// LUM-966 (delete, names Lumogis won't learn, Hide): what a delete removes
// (cascades, search points, graph node, household copy), that a re-read does not
// bring the entry back (block semantics, kind-scoped), that another member cannot
// delete or read someone else's entry or rules, and where Hide applies are all
// computed on the server and proven in
// orchestrator/tests/integration/test_entity_delete_live.py (L1-L11) and
// test_graph_entity_delete_falkordb_live.py (F1-F4). The mocks here only drive the
// flow: menu → confirm → notice → settings list → Remove → re-read offer, and
// Hide → gone → "Show again". Running P1/P2 against a live stack is deferred to
// promotion (needs smoke credentials; web-e2e.yml runs on PRs to main).

import { test, expect, type Page, type Route } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

const OWN_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_ID = "22222222-2222-4222-8222-222222222222";
const DOC_ID = "doc-0161";

interface State {
  ownName: string;
  ownAliases: string[];
  ownVersion: number;
  empty: boolean;
  /** Kind of the member's own entry (LUM-967); defaults to PERSON. */
  ownType?: string;
  /** When set, a kind change answers 409 name_in_use for an entry of this kind. */
  kindClashWith?: string;
  /** Request bodies the PATCH mock received, in order. */
  patches?: Array<Record<string, unknown>>;
}

const ZERO_COUNTS = { person: 0, org: 0, project: 0, concept: 0, other: 0 };

function ownCard(s: State): Record<string, unknown> {
  return {
    entity_id: OWN_ID,
    name: s.ownName,
    type: s.ownType ?? "PERSON",
    aliases: s.ownAliases,
    summary: null,
    sources: [],
    scope: "personal",
    owner_user_id: null,
    share_status: "personal",
    is_owner: true,
    version: s.ownVersion,
  };
}

function otherCard(): Record<string, unknown> {
  return {
    entity_id: OTHER_ID,
    name: "Garage Club",
    type: "ORG",
    aliases: [],
    summary: null,
    sources: [],
    scope: "shared",
    owner_user_id: "someone-else",
    share_status: "shared",
    is_owner: false,
    version: 1,
  };
}

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockKg(page: Page, state: State): Promise<void> {
  await page.route("**/api/v1/memory/search**", (route) =>
    json(route, 200, { hits: [], degraded: false, reason: null }),
  );
  await page.route("**/api/v1/kg/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method();

    if (path === "/api/v1/kg/search") return json(route, 200, { entities: [] });

    if (path === "/api/v1/kg/entities" && method === "GET") {
      if (state.empty) {
        return json(route, 200, { entities: [], counts: ZERO_COUNTS, total: 0 });
      }
      return json(route, 200, {
        entities: [
          {
            entity_id: OWN_ID,
            name: state.ownName,
            type: state.ownType ?? "PERSON",
            kind: (state.ownType ?? "PERSON").toLowerCase(),
            aliases: state.ownAliases,
            alias_count: state.ownAliases.length,
            scope: "personal",
            share_status: "personal",
            is_owner: true,
            mention_count: 2,
            last_mentioned_at: "2026-11-03T10:00:00Z",
          },
          {
            entity_id: OTHER_ID,
            name: "Garage Club",
            type: "ORG",
            kind: "org",
            aliases: [],
            alias_count: 0,
            scope: "shared",
            share_status: "shared",
            is_owner: false,
            mention_count: null,
            last_mentioned_at: null,
          },
        ],
        counts: { ...ZERO_COUNTS, person: 1, org: 1 },
        total: 2,
      });
    }

    if (path === `/api/v1/kg/entities/${OWN_ID}/mentions`) {
      return json(route, 200, {
        mentions: [
          { kind: "document", ref_id: DOC_ID, title: "Meeting notes.pdf", mentioned_at: "2026-11-03T10:00:00Z" },
          { kind: "chat", ref_id: "s-1", title: "Trip planning", mentioned_at: "2026-11-02T10:00:00Z" },
        ],
        counts: { document: 1, chat: 1, ai_tool: 0 },
        total: 2,
      });
    }
    if (path === `/api/v1/kg/entities/${OTHER_ID}/mentions`) {
      return json(route, 200, {
        mentions: [],
        counts: { document: 0, chat: 0, ai_tool: 0 },
        total: 0,
      });
    }
    if (path.endsWith("/related")) return json(route, 200, { related: [] });

    if (path === `/api/v1/kg/entities/${OWN_ID}`) {
      if (method === "PATCH") {
        const body = req.postDataJSON() as {
          name?: string;
          aliases_add?: string[];
          entity_type?: string;
          version: number;
        };
        (state.patches ??= []).push(body);
        if (body.entity_type) {
          if (state.kindClashWith && body.entity_type === state.kindClashWith) {
            return json(route, 409, {
              detail: {
                error: "name_in_use",
                code: "name_in_use",
                conflicting_entity_id: OTHER_ID,
                conflicting_name: state.ownName,
                name: state.ownName,
                conflicting_is_staged: false,
                conflicting_entity_type: state.kindClashWith,
              },
            });
          }
          state.ownType = body.entity_type;
          state.ownVersion += 1;
          return json(route, 200, ownCard(state));
        }
        if (body.name && body.name !== state.ownName) {
          // The server keeps the old name as another name (plan Decision 4).
          state.ownAliases = [...state.ownAliases, state.ownName, ...(body.aliases_add ?? [])];
          state.ownName = body.name;
        }
        state.ownVersion += 1;
        return json(route, 200, ownCard(state));
      }
      return json(route, 200, ownCard(state));
    }
    if (path === `/api/v1/kg/entities/${OTHER_ID}`) return json(route, 200, otherCard());

    return route.continue();
  });
}

const THIRD_ID = "33333333-3333-4333-8333-333333333333";
const MERGE_ID = "44444444-4444-4444-8444-444444444444";

interface MergeState {
  merged: boolean;
  undone: boolean;
  moved: boolean;
  bodies: Array<{ path: string; body: unknown }>;
}

function thirdCard(): Record<string, unknown> {
  return {
    entity_id: THIRD_ID,
    name: "Dr. A. Meier",
    type: "PERSON",
    aliases: [],
    summary: null,
    sources: [],
    scope: "personal",
    owner_user_id: null,
    share_status: "personal",
    is_owner: true,
    version: 2,
  };
}

/** LUM-965: layered over mockKg (registered later, so it runs first and falls back). */
async function mockMerge(page: Page, state: State, ms: MergeState): Promise<void> {
  await page.route("**/api/v1/kg/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method();

    if (path === "/api/v1/kg/search") {
      return json(route, 200, { entities: [{ ...thirdCard(), version: null }] });
    }
    if (path === `/api/v1/kg/entities/${THIRD_ID}` && method === "GET") {
      return json(route, 200, thirdCard());
    }
    if (path === `/api/v1/kg/entities/${THIRD_ID}/mentions`) {
      const mentions = ms.moved
        ? [{ kind: "document", ref_id: DOC_ID, title: "Letter.pdf", mentioned_at: "2026-11-12T10:00:00Z" }]
        : [];
      return json(route, 200, {
        mentions,
        counts: { document: mentions.length, chat: 0, ai_tool: 0 },
        total: mentions.length,
      });
    }
    if (path === `/api/v1/kg/entities/${OWN_ID}/merge` && method === "POST") {
      ms.bodies.push({ path, body: req.postDataJSON() });
      ms.merged = true;
      state.ownAliases = [...state.ownAliases, "Dr. A. Meier"];
      state.ownVersion += 1;
      return json(route, 200, { entry: ownCard(state), merge_id: MERGE_ID });
    }
    if (path === `/api/v1/kg/entities/${OWN_ID}/history`) {
      const items = ms.merged
        ? [
            {
              merge_id: MERGE_ID,
              direction: "merged_in",
              other_entity_id: THIRD_ID,
              other_name: "Dr. A. Meier",
              origin: "member",
              merged_at: "2026-11-10T10:00:00Z",
              undone_at: ms.undone ? "2026-11-11T10:00:00Z" : null,
              can_undo: !ms.undone,
              newer_merge_id: null,
              restores_sharing: false,
            },
          ]
        : [];
      return json(route, 200, { items });
    }
    if (path === `/api/v1/kg/merges/${MERGE_ID}/undo` && method === "POST") {
      ms.undone = true;
      state.ownAliases = state.ownAliases.filter((a) => a !== "Dr. A. Meier");
      return json(route, 200, {
        restored: thirdCard(),
        survivor: ownCard(state),
        mentions_since: [
          {
            kind: "document",
            ref_id: DOC_ID,
            title: "Letter.pdf",
            mentioned_at: "2026-11-12T10:00:00Z",
            relation_id: 7,
          },
        ],
        skipped_rows: 0,
      });
    }
    if (path === `/api/v1/kg/entities/${OWN_ID}/mentions/move` && method === "POST") {
      ms.bodies.push({ path, body: req.postDataJSON() });
      ms.moved = true;
      return json(route, 200, {
        moved: 1,
        source: ownCard(state),
        target: thirdCard(),
      });
    }
    return route.fallback();
  });
}

interface DeleteState {
  deleted: boolean;
  blockRemoved: boolean;
  hidden: boolean;
  bodies: Array<{ path: string; method: string; body?: unknown }>;
}

/** LUM-966: layered over mockKg (registered later, so it runs first and falls back). */
async function mockDelete(page: Page, state: State, ds: DeleteState): Promise<void> {
  await page.route("**/api/v1/kg/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method();

    if (path === `/api/v1/kg/entities/${OWN_ID}` && method === "DELETE") {
      ds.bodies.push({ path, method });
      ds.deleted = true;
      return json(route, 200, {
        deleted: true,
        entity_id: OWN_ID,
        blocked_names: [state.ownName, ...state.ownAliases],
        not_blocked: [],
        household_copy_removed: false,
        merges_removed: 0,
      });
    }
    if (path === `/api/v1/kg/entities/${OWN_ID}/history`) return json(route, 200, { items: [] });

    if (path === "/api/v1/kg/entities" && method === "GET") {
      const entities = [];
      if (!ds.deleted) {
        entities.push({
          entity_id: OWN_ID,
          name: state.ownName,
          type: "PERSON",
          kind: "person",
          aliases: state.ownAliases,
          alias_count: state.ownAliases.length,
          scope: "personal",
          share_status: "personal",
          is_owner: true,
          mention_count: 2,
          last_mentioned_at: "2026-11-03T10:00:00Z",
        });
      }
      if (!ds.hidden) {
        entities.push({
          entity_id: OTHER_ID,
          name: "Garage Club",
          type: "ORG",
          kind: "org",
          aliases: [],
          alias_count: 0,
          scope: "shared",
          share_status: "shared",
          is_owner: false,
          mention_count: null,
          last_mentioned_at: null,
        });
      }
      return json(route, 200, {
        entities,
        counts: {
          ...ZERO_COUNTS,
          person: ds.deleted ? 0 : 1,
          org: ds.hidden ? 0 : 1,
        },
        total: entities.length,
      });
    }

    if (path === `/api/v1/kg/entities/${OTHER_ID}` && method === "GET") {
      return json(route, 200, { ...otherCard(), hidden: ds.hidden });
    }
    if (path === `/api/v1/kg/entities/${OTHER_ID}/hide`) {
      ds.bodies.push({ path, method });
      ds.hidden = method === "POST";
      return json(route, 200, { entity_id: OTHER_ID, hidden: ds.hidden });
    }

    if (path === "/api/v1/kg/name-rules" && method === "GET") {
      const blocked =
        ds.deleted && !ds.blockRemoved
          ? [
              {
                name_norm: "dr. meier",
                display_name: state.ownName,
                entity_type: "PERSON",
                document_count: 1,
                created_at: "2026-11-17T10:00:00Z",
              },
            ]
          : [];
      const hidden = ds.hidden
        ? [{ entity_id: OTHER_ID, name: "Garage Club", type: "ORG", hidden_at: "2026-11-17T10:05:00Z" }]
        : [];
      return json(route, 200, {
        blocked,
        corrections: [],
        hidden,
        blocked_total: blocked.length,
        corrections_total: 0,
        hidden_total: hidden.length,
      });
    }
    if (path === "/api/v1/kg/name-rules/remove" && method === "POST") {
      ds.bodies.push({ path, method, body: req.postDataJSON() });
      ds.blockRemoved = true;
      return json(route, 200, {
        removed: true,
        restored_corrections: [],
        reread_documents: [{ document_id: 7, title: "Meeting notes.pdf" }],
        reread_more: 0,
      });
    }
    return route.fallback();
  });
}

const VIEWPORTS = [
  { name: "desktop", size: { width: 1280, height: 800 } },
  { name: "mobile", size: { width: 390, height: 844 } },
];

// One smoke user: parallel logins revoke each other's refresh token.
test.describe.configure({ mode: "serial" });

for (const vp of VIEWPORTS) {
  test.describe(`LUM-161 What Lumogis knows (${vp.name})`, () => {
    test.use({ viewport: vp.size });
    test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

    test("zero state when Lumogis has learned nothing", async ({ page }) => {
      await mockKg(page, { ownName: "Dr. Meier", ownAliases: [], ownVersion: 1, empty: true });
      await loginWithSmokeCredentials(page);
      await page.goto("/entities");
      await expect(page.getByRole("heading", { name: "What Lumogis knows" })).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByText("Lumogis hasn't learned anything yet")).toBeVisible();
      await expect(page.getByRole("link", { name: "Add documents" })).toBeVisible();
    });

    test("Search → Browse → list → entry → rename; other member's entry has no Edit", async ({
      page,
    }) => {
      const state: State = {
        ownName: "Dr. Meier",
        ownAliases: ["Meier"],
        ownVersion: 1,
        empty: false,
      };
      await mockKg(page, state);
      await loginWithSmokeCredentials(page);

      // Search page links to the browser; no new nav item, Search stays highlighted.
      await page.goto("/search");
      await page.getByRole("link", { name: /Browse what Lumogis knows/ }).click();
      await expect(page).toHaveURL(/\/entities$/);
      await expect(page.getByRole("heading", { name: "What Lumogis knows" })).toBeVisible();
      await expect(page.locator('a[aria-current="page"]:visible').first()).toContainText(
        /search/i,
      );

      // Counts per kind and the rows.
      await expect(page.getByRole("tab", { name: "All (2)" })).toBeVisible();
      await expect(page.getByRole("tab", { name: "People (1)" })).toBeVisible();
      await expect(page.getByRole("tab", { name: "Organisations (1)" })).toBeVisible();
      await expect(page.getByText("Mentioned in 2 items")).toBeVisible();

      // Entry page: "Mentioned in" links the document, not the chat.
      await page.getByRole("link", { name: "Dr. Meier" }).click();
      await expect(page).toHaveURL(new RegExp(`/entities/${OWN_ID}$`));
      await expect(page.getByText("Mentioned in 1 document, 1 chat")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByRole("link", { name: "Meeting notes.pdf" })).toHaveAttribute(
        "href",
        `/documents/${DOC_ID}`,
      );
      await expect(page.getByRole("link", { name: /Trip planning/ })).toHaveCount(0);

      // Owner renames; the entry page shows the new name, the old one is kept.
      await page.getByRole("button", { name: "Edit names" }).click();
      const dialog = page.getByRole("dialog");
      const nameInput = dialog.getByLabel("Name", { exact: true });
      await nameInput.fill("Hans Meier");
      await expect(dialog.getByText("kept")).toBeVisible();
      await dialog.getByRole("button", { name: "Save" }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
      await expect(page.getByRole("heading", { name: "Hans Meier" })).toBeVisible();

      // Back on the list the new name shows.
      await page.getByRole("link", { name: "← What Lumogis knows" }).click();
      await expect(page.getByRole("link", { name: "Hans Meier" })).toBeVisible();

      // Another member's shared entry: no Edit button, and the viewer note shows.
      await page.getByRole("link", { name: "Garage Club" }).click();
      await expect(page.getByRole("heading", { name: "Garage Club" })).toBeVisible();
      await expect(page.getByText("Only items shared with the household are listed.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Edit names" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^This is wrong…/ })).toHaveCount(0);
    });

    test("owner corrects the kind; a clash is explained; another member's entry has no menu", async ({
      page,
    }) => {
      const state: State = {
        ownName: "Meier Pharmacy",
        ownAliases: [],
        ownVersion: 4,
        empty: false,
        patches: [],
      };
      await mockKg(page, state);
      await loginWithSmokeCredentials(page);
      await page.goto(`/entities/${OWN_ID}`);
      await expect(page.getByRole("heading", { name: "Meier Pharmacy" })).toBeVisible({
        timeout: 15_000,
      });

      // Clash first: the server answers 409 for an organisation of that name.
      state.kindClashWith = "ORG";
      await page.getByRole("button", { name: /^This is wrong…/ }).click();
      await page.getByRole("menuitem", { name: "It's not a person, it's a…" }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("radio", { name: "Person" })).toHaveCount(0);
      await expect(dialog.getByText(/Lumogis will remember this/)).toBeVisible();
      await dialog.getByRole("radio", { name: "Organisation" }).check();
      await dialog.getByRole("button", { name: "Change" }).click();
      await expect(
        dialog.getByText(/You already have an organisation called “Meier Pharmacy”/),
      ).toBeVisible();
      await expect(dialog.getByRole("link", { name: "View that entry" })).toHaveAttribute(
        "href",
        `/entities/${OTHER_ID}`,
      );

      // Without the clash the change goes through and the entry page shows the new kind.
      state.kindClashWith = undefined;
      await dialog.getByRole("radio", { name: "Project" }).check();
      await dialog.getByRole("button", { name: "Change" }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
      expect(state.patches?.[state.patches.length - 1]).toEqual({
        entity_type: "PROJECT",
        version: 4,
      });
      await page.getByRole("button", { name: /^This is wrong…/ }).click();
      await expect(page.getByRole("menuitem", { name: "It's not a project, it's a…" })).toBeVisible();

      // Another member's entry has no menu.
      await page.goto(`/entities/${OTHER_ID}`);
      await expect(page.getByRole("heading", { name: "Garage Club" })).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByRole("button", { name: /^This is wrong…/ })).toHaveCount(0);
    });

    test("P1/P2 owner merges two entries, sees it in History, undoes it and moves a mention", async ({
      page,
    }) => {
      const state: State = {
        ownName: "Dr. Meier",
        ownAliases: [],
        ownVersion: 4,
        empty: false,
        patches: [],
      };
      const ms: MergeState = { merged: false, undone: false, moved: false, bodies: [] };
      await mockKg(page, state);
      await mockMerge(page, state, ms);
      await loginWithSmokeCredentials(page);
      await page.goto(`/entities/${OWN_ID}`);
      await expect(page.getByRole("heading", { name: "Dr. Meier" })).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("entity-history")).toHaveCount(0); // nothing to show yet

      // P1: menu → pick → confirm → one entry, the other name under "Also known as", History.
      await page.getByRole("button", { name: /^This is wrong…/ }).click();
      await page.getByRole("menuitem", { name: "It's the same as…" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("searchbox").fill("Meier");
      await dialog.getByRole("button", { name: /Dr\. A\. Meier/ }).click();
      await expect(dialog.getByText(/You can undo this from the entry's History/)).toBeVisible();
      await dialog.getByRole("button", { name: "Merge" }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
      expect(ms.bodies[0]!.body).toEqual({
        other_id: THIRD_ID,
        version: 4,
        other_version: 2,
        keep_name: "this",
      });
      await expect(page.getByRole("heading", { name: "Also known as" })).toBeVisible();
      await expect(page.getByText("Dr. A. Meier").first()).toBeVisible();
      const history = page.getByTestId("entity-history");
      await expect(history.getByText(/Merged “Dr\. A\. Meier” into this entry/)).toBeVisible();

      // P2: Undo → confirm → the mover lists the mention → move → the restored page lists it.
      await history.getByRole("button", { name: "Undo merge" }).click();
      await page
        .getByRole("alertdialog", { name: "Undo this merge?" })
        .getByRole("button", { name: "Undo merge" })
        .click();
      await expect(history.getByText("“Dr. A. Meier” is back as its own entry.")).toBeVisible();
      await history.getByRole("checkbox", { name: /Letter\.pdf/ }).check();
      await history.getByRole("button", { name: "Move to “Dr. A. Meier”" }).click();
      await expect(history.getByRole("status")).toContainText("Moved 1 item");
      expect(ms.bodies[1]!.body).toMatchObject({ to_entity_id: THIRD_ID, relation_ids: [7] });
      await page.goto(`/entities/${THIRD_ID}`);
      await expect(page.getByRole("link", { name: "Letter.pdf" })).toBeVisible({ timeout: 15_000 });
    });

    test("LUM-966 P1 owner deletes an entry, sees what Lumogis won't learn, removes the name", async ({
      page,
    }) => {
      const state: State = {
        ownName: "Dr. Meier",
        ownAliases: [],
        ownVersion: 4,
        empty: false,
        patches: [],
      };
      const ds: DeleteState = { deleted: false, blockRemoved: false, hidden: false, bodies: [] };
      await mockKg(page, state);
      await mockDelete(page, state, ds);
      await loginWithSmokeCredentials(page);
      await page.goto(`/entities/${OWN_ID}`);
      await expect(page.getByRole("heading", { name: "Dr. Meier" })).toBeVisible({
        timeout: 15_000,
      });

      // Menu → Delete… → the dialog says what happens; Cancel has focus.
      await page.getByRole("button", { name: /^This is wrong…/ }).click();
      await page.getByRole("menuitem", { name: "Delete…" }).click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog.getByRole("heading", { name: "Delete “Dr. Meier”?" })).toBeVisible();
      await expect(dialog.getByText(/Lumogis won't learn “Dr\. Meier” as a person again/)).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
      await dialog.getByRole("button", { name: "Delete", exact: true }).click();

      // Back on the list without the entry; the notice names what Lumogis won't learn.
      await expect(page).toHaveURL(/\/entities$/, { timeout: 15_000 });
      expect(ds.bodies[0]).toMatchObject({ method: "DELETE" });
      await expect(page.getByRole("link", { name: "Dr. Meier" })).toHaveCount(0);
      await expect(page.getByTestId("entity-deleted-notice")).toContainText(
        "“Dr. Meier” is on your list of names Lumogis won't learn.",
      );

      // Settings lists it; Remove offers a re-read, and "Not now" dismisses the offer.
      await page.goto("/settings/wont-learn");
      const row = page.getByRole("listitem").filter({ hasText: "Dr. Meier as a person" });
      await expect(row).toBeVisible({ timeout: 15_000 });
      await row.getByRole("button", { name: "Remove" }).click();
      await expect(page.getByText("Re-read 1 document that mentioned it?")).toBeVisible();
      expect(ds.bodies[1]).toMatchObject({
        path: "/api/v1/kg/name-rules/remove",
        body: { kind: "block", name_norm: "dr. meier", entity_type: "PERSON" },
      });
      await page.getByRole("button", { name: "Not now" }).click();
      await expect(page.getByText("Re-read 1 document that mentioned it?")).toHaveCount(0);
      await expect(row).toHaveCount(0);
    });

    test("LUM-966 P2 another member's shared entry is hidden from the list, then shown again", async ({
      page,
    }) => {
      const state: State = {
        ownName: "Dr. Meier",
        ownAliases: [],
        ownVersion: 4,
        empty: false,
      };
      const ds: DeleteState = { deleted: false, blockRemoved: false, hidden: false, bodies: [] };
      await mockKg(page, state);
      await mockDelete(page, state, ds);
      await loginWithSmokeCredentials(page);
      await page.goto(`/entities/${OTHER_ID}`);
      await expect(page.getByRole("heading", { name: "Garage Club" })).toBeVisible({
        timeout: 15_000,
      });

      // Not the owner: Hide instead of a "This is wrong…" menu with Delete.
      await expect(page.getByRole("button", { name: /^This is wrong…/ })).toHaveCount(0);
      await page.getByRole("button", { name: "Hide from my list" }).click();
      await expect(page.getByText("Hidden from your list.")).toBeVisible({ timeout: 15_000 });

      await page.goto("/entities");
      await expect(page.getByRole("heading", { name: "What Lumogis knows" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Garage Club" })).toHaveCount(0);

      // Settings lists it under "Hidden from your list"; Show again brings it back.
      await page.goto("/settings/wont-learn");
      const hiddenRow = page.getByRole("listitem").filter({ hasText: "Garage Club" });
      await expect(hiddenRow).toBeVisible({ timeout: 15_000 });
      await hiddenRow.getByRole("button", { name: "Show again" }).click();
      await expect(hiddenRow).toHaveCount(0);
      await page.goto("/entities");
      await expect(page.getByRole("link", { name: "Garage Club" })).toBeVisible({ timeout: 15_000 });
    });
  });
}
