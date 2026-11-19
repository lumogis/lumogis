// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-450 — admin Ollama pull/delete via async pull (LUM-449) on /settings/health.
// Hermetic: pull ephemeral model, wait for async job UI, delete same model.
//
//   export LUMOGIS_WEB_SMOKE_EMAIL=...
//   export LUMOGIS_WEB_SMOKE_PASSWORD='...'   # ≥12 chars
//   export LUMOGIS_E2E_EXPECT_ADMIN=1
//   export LUMOGIS_E2E_EXPECT_OLLAMA=1
//
// Optional: LUMOGIS_E2E_OLLAMA_PULL_MODEL=tinyllama:1.1b (default)
// Optional: PLAYWRIGHT_BASE_URL=http://127.0.0.1

import { test, expect } from "@playwright/test";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

const expectAdmin =
  process.env.LUMOGIS_E2E_EXPECT_ADMIN === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_ADMIN=1 (admin smoke user required).";

const expectOllama =
  process.env.LUMOGIS_E2E_EXPECT_OLLAMA === "1"
    ? ""
    : "Set LUMOGIS_E2E_EXPECT_OLLAMA=1 (full compose with ollama service required).";

const pullModel =
  process.env.LUMOGIS_E2E_OLLAMA_PULL_MODEL?.trim() || "tinyllama:1.1b";

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test.describe("LUM-450 admin Ollama mutations", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.skip(!!expectAdmin, expectAdmin);
  test.skip(!!expectOllama, expectOllama);

  test("pull then delete ephemeral model", async ({ page }) => {
    test.setTimeout(600_000);

    await loginWithSmokeCredentials(page);

    await page.goto("/settings/health");
    await expect(page).toHaveURL(/\/settings\/health$/);

    const section = page.getByTestId("system-status");
    await expect(section).toBeVisible();

    await expect(section.getByText(/^loading/i)).toHaveCount(0, { timeout: 60_000 });

    const pullInput = section.getByLabel("Ollama model name to pull");
    await expect(pullInput).toBeVisible({ timeout: 30_000 });

    const modelRow = () => section.getByRole("row").filter({ hasText: pullModel });
    const pullingCopy = section.getByText(
      new RegExp(`Pulling ${escapeRegex(pullModel)}`, "i"),
    );
    const progressBar = section.getByLabel("Ollama pull progress");
    const inFlightUi = progressBar.or(pullingCopy);

    await pullInput.fill(pullModel);
    await section.getByRole("button", { name: /^pull$/i }).click();

    let sawInFlight = false;
    try {
      await expect(inFlightUi.first()).toBeVisible({ timeout: 30_000 });
      sawInFlight = true;
    } catch {
      await page.waitForTimeout(5_000);
      if ((await modelRow().count()) > 0) {
        throw new Error(
          `Model row for "${pullModel}" visible without in-flight pull UI — delete leftover model and retry.`,
        );
      }
    }

    if (sawInFlight) {
      await expect(progressBar).toHaveCount(0, { timeout: 540_000 });
      await expect(pullingCopy).toHaveCount(0, { timeout: 10_000 });
      await expect(section.getByRole("button", { name: /^pull$/i })).toBeEnabled();
    }

    await expect(modelRow()).toBeVisible({ timeout: 60_000 });

    await modelRow().getByRole("button", { name: /^delete /i }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm).toContainText(`Remove "${pullModel}"`);
    await confirm.getByRole("button", { name: "Delete model" }).click();

    await expect(modelRow()).toHaveCount(0, { timeout: 60_000 });
    // LUM-792 — scoped to the Ollama panel, NOT the whole System status section. The
    // section also holds the DR-backup staleness advisory, which is permanently present
    // on a fresh CI stack (no backup has ever run there), so the old section-wide
    // `toHaveCount(0)` could never pass in CI no matter how well the delete worked.
    // Narrowing does not weaken the check: the mutation error this test cares about
    // (`ollamaActionError`) renders inside this panel, so a real delete failure is still
    // caught — only unrelated advisories are excluded.
    await expect(
      page.getByRole("region", { name: /^ollama models$/i }).getByRole("alert"),
    ).toHaveCount(0);
  });
});
