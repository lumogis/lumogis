// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The Chat home (LUM-993 Pass 2b), against a running stack with smoke
// credentials: an empty chat shows the heading and the composer, centred; the
// model chip and Send sit inside the composer; one history line with an (i)
// replaces the old "ephemeral" sentences; on a phone the chat list opens from
// "Chats", and the composer stays on screen in a short viewport.
//
// The keyboard case is a 390×500 viewport in Chromium. Safari ignores
// `interactive-widget=resizes-content`, so the real iPhone keyboard is the
// manual Safari check in the plan's Definition of done.

import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { hasSmokeCreds, loginWithSmokeCredentials, smokeCredsSkipMessage } from "./smoke-auth";

async function openEmptyChat(page: Page): Promise<void> {
  await loginWithSmokeCredentials(page);
  await page.goto("/chat");
  await expect(page.getByTestId("chat-page")).toBeVisible({ timeout: 30_000 });
  // A thread restored from this tab may not be empty; start a fresh one.
  if (!(await page.getByTestId("chat-home").isVisible().catch(() => false))) {
    await page.getByRole("button", { name: /^(New chat|Start a new chat)$/ }).first().click();
  }
  await expect(page.getByTestId("chat-home")).toBeVisible();
}

test.describe("Chat home, wide screen", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 1280, height: 800 } });

  test("heading and composer centred; chip and Send inside; one history line; axe clean", async ({ page }) => {
    await openEmptyChat(page);
    const home = page.getByTestId("chat-home");
    await expect(home.getByRole("heading", { level: 1, name: "What can I help you with?" })).toBeVisible();

    const form = home.locator("form").filter({ has: page.getByLabel("Message", { exact: true }) });
    await expect(form.getByRole("combobox", { name: "Model" })).toBeVisible({ timeout: 30_000 });
    await expect(form.getByRole("button", { name: "Send" })).toBeDisabled();

    // Centred in the chat column (within a few pixels).
    const col = await page.getByRole("region", { name: "Chat" }).boundingBox();
    const box = await form.boundingBox();
    expect(col && box).toBeTruthy();
    const colMid = col!.x + col!.width / 2;
    const boxMid = box!.x + box!.width / 2;
    expect(Math.abs(colMid - boxMid)).toBeLessThan(8);

    await expect(page.getByText("Open chats stay in this tab. Closed chats are kept as summaries in History.")).toBeVisible();
    await expect(page.getByRole("button", { name: "About chat history" })).toBeVisible();
    await expect(page.getByText(/ephemeral/i)).toHaveCount(0);

    await form.getByLabel("Message", { exact: true }).fill("Hello");
    await expect(form.getByRole("button", { name: "Send" })).toBeEnabled();

    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious, JSON.stringify(serious, null, 2)).toHaveLength(0);
  });
});

test.describe("Chat home, phone", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test("the chat list opens from Chats; no sideways scroll", async ({ page }) => {
    await openEmptyChat(page);
    await expect(page.getByRole("complementary", { name: "Conversations" })).toBeHidden();
    await page.getByRole("button", { name: "Chats" }).click();
    const sheet = page.getByRole("dialog", { name: "Chats" });
    await expect(sheet.getByRole("button", { name: "New chat", exact: true })).toBeVisible();
    await expect(sheet.getByRole("heading", { name: "History" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    const over = await page.evaluate(() => {
      const main = document.getElementById("lumogis-main");
      return Math.max(document.body.scrollWidth - document.body.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
    });
    expect(over).toBeLessThanOrEqual(1);
  });
});

test.describe("Chat home, phone with the keyboard open", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);
  test.use({ viewport: { width: 390, height: 500 }, hasTouch: true });

  test("the composer stays on screen", async ({ page }) => {
    await openEmptyChat(page);
    const input = page.getByTestId("chat-home").getByLabel("Message", { exact: true });
    await input.click();
    await input.fill("Is the composer still visible?");
    await expect(input).toBeInViewport();
    await expect(page.getByTestId("chat-home").getByRole("button", { name: "Send" })).toBeInViewport();
  });
});
