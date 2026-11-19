// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-849 — browser leg for the autonomy page, folded into the LUM-842 run
// because this machine has the stack the container did not.
//
// Unit coverage already pins copy, degrade, and the per-group mutation. What a
// browser adds: the two questions actually render in the shell, a group
// disables while its PUT is in flight (so a second click cannot write the same
// permission change twice), and a double-click fires exactly one PUT.
//
// Hybrid: smoke login + route mocks. The fake PUT hangs until the test
// releases it — a wall-clock delay raced the vitest suite on this host.

import { test, expect, type Page } from "@playwright/test";

import {
  hasSmokeCreds,
  loginWithSmokeCredentials,
  smokeCredsSkipMessage,
} from "./smoke-auth";

function row(connector: string, mode: "ASK" | "DO", is_default = true) {
  return {
    connector,
    mode,
    scopes: [],
    is_default,
    updated_at: "2026-01-01T00:00:00Z",
  };
}

interface PermissionPut {
  pathname: string;
  connector: string;
  body: { mode?: "ASK" | "DO" };
}

async function installPermissionMocks(
  page: Page,
  opts: { holdPuts?: boolean } = {},
): Promise<{ puts: () => PermissionPut[]; releasePuts: () => void }> {
  const puts: PermissionPut[] = [];
  let release!: () => void;
  const hold = opts.holdPuts
    ? new Promise<void>((r) => {
        release = r;
      })
    : Promise.resolve();

  await page.route("**/api/v1/me/permissions**", async (route) => {
    const method = route.request().method();
    const path = new URL(route.request().url()).pathname;
    if (method === "PUT" && path.startsWith("/api/v1/me/permissions/")) {
      const connector = decodeURIComponent(path.split("/me/permissions/")[1] ?? "");
      const body = (route.request().postDataJSON() ?? {}) as PermissionPut["body"];
      puts.push({ pathname: path, connector, body });
      await hold;
      const mode = body.mode ?? "ASK";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(row(connector, mode, false)),
      });
      return;
    }
    if (method === "GET" && path === "/api/v1/me/permissions") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([row("smtp", "ASK"), row("imap", "ASK")]),
      });
      return;
    }
    await route.continue();
  });

  return {
    puts: () => puts,
    releasePuts: () => release?.(),
  };
}

test.describe("autonomy page (LUM-849)", () => {
  test.skip(!hasSmokeCreds, smokeCredsSkipMessage);

  test.beforeEach(async ({ page }) => {
    await loginWithSmokeCredentials(page);
  });

  test("two questions render in words, not connector ids", async ({ page }) => {
    await installPermissionMocks(page);
    await page.goto("/settings/permissions");
    await expect(page.getByRole("heading", { level: 1, name: "What Lumogis may do" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Send email as you")).toBeVisible();
    await expect(page.getByText("Tidy your mailbox")).toBeVisible();

    const text = await page.locator("section").first().innerText();
    for (const raw of ["imap", "smtp", "ASK", "DO"]) {
      expect(text).not.toContain(raw);
    }
  });

  test("a group disables while its save is in flight", async ({ page }) => {
    const rec = await installPermissionMocks(page, { holdPuts: true });
    await page.goto("/settings/permissions");
    await expect(page.getByText("Send email as you")).toBeVisible({ timeout: 15_000 });

    const smtpGroup = page.getByTestId("perm-group-smtp");
    const imapGroup = page.getByTestId("perm-group-imap");
    await smtpGroup.getByRole("radio", { name: /Just send/i }).click();
    await expect(smtpGroup.getByRole("radio", { name: /Just send/i })).toBeDisabled();
    await expect(smtpGroup.getByRole("radio", { name: /Ask me first/i })).toBeDisabled();
    await expect(imapGroup.getByRole("radio").first()).toBeEnabled();
    rec.releasePuts();
  });

  test("a double-click fires exactly one PUT", async ({ page }) => {
    const rec = await installPermissionMocks(page, { holdPuts: true });
    await page.goto("/settings/permissions");
    await expect(page.getByText("Send email as you")).toBeVisible({ timeout: 15_000 });

    const justSend = page.getByTestId("perm-group-smtp").getByRole("radio", { name: /Just send/i });
    await justSend.click();
    await justSend.click({ force: true });
    expect(rec.puts()).toHaveLength(1);
    expect(rec.puts()[0]?.pathname).toBe("/api/v1/me/permissions/smtp");
    expect(rec.puts()[0]?.connector).toBe("smtp");
    expect(rec.puts()[0]?.body).toEqual({ mode: "DO" });
    rec.releasePuts();
  });
});
