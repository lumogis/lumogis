// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-937 — Admin → Knowledge graph, end to end on a live stack: the admin nav
// reaches the page and the page renders against the real route.
//
// Nothing here is mocked (no `page.route`): mocking /api/v1/admin/knowledge-graph
// would only re-assert the fixture.
//
// Run (see `make web-e2e-prove`):
//   export LUMOGIS_WEB_SMOKE_EMAIL=...  LUMOGIS_WEB_SMOKE_PASSWORD='...'  # >= 12 chars
//   export LUMOGIS_E2E_EXPECT_ADMIN=1
//   npx playwright test tests/e2e/admin_knowledge_graph.spec.ts --project=chromium-smoke-shared-user
//
// NOT covered here, on purpose (vacuous-test principle: do not assert
// server-computed logic through the browser):
//   - which state the stack is in, and the memory verdict: computed on the
//     server and depend on the host; covered by the pytest truth table
//     (test_graph_status.py, test_api_v1_admin_knowledge_graph.py).
//   - the `on` and `service_unreachable` wording: needs the 4 GB graph overlay
//     running; covered in Vitest (knowledgeGraphCopy.test.ts,
//     AdminKnowledgeGraphView.test.tsx).
//   - member denial: pytest 403; the nav is behind the existing admin RoleGate.

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const expectAdmin =
  process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_ADMIN=1 (admin smoke user required).";

const QUICKSTART_ANCHOR = "quickstart.md#turn-on-the-knowledge-graph";

test.describe("LUM-937 admin knowledge graph page (live)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!!expectAdmin, expectAdmin);

  test("desktop: the Settings menu opens the page (Advanced group)", async ({ page }) => {
    await loginWithSmokeCredentials(page);
    await page.goto("/settings/members");
    const menu = page.getByRole("navigation", { name: "Settings" });
    await menu.getByRole("button", { name: "Advanced" }).click();
    await menu.getByRole("link", { name: "Knowledge graph" }).click();
    await expect(page).toHaveURL(/\/settings\/knowledge-graph$/);
    await expect(page.getByRole("heading", { level: 1, name: "Knowledge graph" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: /knowledge graph|GRAPH_MODE/i })).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByRole("heading", { name: "What the graph adds" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Turn on the knowledge graph (full steps)" })).toHaveAttribute(
      "href",
      new RegExp(QUICKSTART_ANCHOR),
    );
  });

  test.describe("mobile (390x844)", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("the page is reachable and does not overflow", async ({ page }) => {
      await loginWithSmokeCredentials(page);
      await page.goto("/settings");
      await page.getByRole("link", { name: /^Knowledge graph/ }).click();
      await expect(page).toHaveURL(/\/settings\/knowledge-graph$/);
      await expect(page.getByRole("heading", { name: "What the graph adds" })).toBeVisible({
        timeout: 60_000,
      });
      const section = page.getByTestId("knowledge-graph-page");
      const fits = await section.evaluate((el) => el.scrollWidth <= el.clientWidth + 1);
      expect(fits).toBe(true);
    });
  });
});
