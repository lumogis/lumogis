// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LAUNCH DEMO recording (LUM-181) — the two-user household knowledge-base flow.
// This is NOT a CI assertion spec: it drives the REAL backend (real ingest,
// real share, real member search + document-chat) and its job is to produce a
// clean, paced video. Run with playwright.demo.config.ts, then demo-to-gif.sh.
//
// Scenes:
//   1. Admin uploads a household document (real ingest → progress → done).
//   2. Admin shares it with the household (the differentiating beat).
//   3. Member (a SECOND account) searches and finds the shared doc.
//   4. Member opens document-chat and asks — grounded answer with a citation.
//
// Requires two real accounts on a live stack (see playwright.demo.config.ts):
//   LUMOGIS_WEB_SMOKE_EMAIL / _PASSWORD  → the admin
//   DEMO_MEMBER_EMAIL / DEMO_MEMBER_PASSWORD → the household member
// The member account must already exist (create it once via the invite flow,
// or the household_invite.spec.ts helper).

import { test, type BrowserContext, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEMO_DIR = path.dirname(fileURLToPath(import.meta.url));
const VIDEO_DIR = "test-results/demo/video";
const SAMPLE_DOC = path.join(DEMO_DIR, "fixtures", "household-insurance.md");
const SAMPLE_DOC_NAME = "household-insurance.md";
const MEMBER_QUERY = "what is our home insurance excess";
const CHAT_QUESTION = "What's our excess on the home insurance?";

const ADMIN = {
  email: process.env.LUMOGIS_WEB_SMOKE_EMAIL ?? "",
  password: process.env.LUMOGIS_WEB_SMOKE_PASSWORD ?? "",
};
const MEMBER = {
  email: process.env.DEMO_MEMBER_EMAIL ?? "",
  password: process.env.DEMO_MEMBER_PASSWORD ?? "",
};

/** Deliberate pause so the recording is watchable (not machine-fast). */
const beat = (page: Page, ms = 1200) => page.waitForTimeout(ms);

/** Type like a human — reads far better on camera than an instant fill(). */
async function humanType(_page: Page, selector: () => ReturnType<Page["locator"]>, text: string) {
  await selector().click();
  await selector().pressSequentially(text, { delay: 55 });
}

/** Login mirroring smoke-auth.ts::loginWithSmokeCredentials but parameterised. */
async function loginAs(page: Page, email: string, password: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  const pw = page.getByLabel("Password", { exact: true });
  await pw.fill(password);
  await pw.press("Enter");
  await page.waitForURL(/\/chat$/, { timeout: 60_000 });
  // Dismiss first-run onboarding if shown (no-op otherwise).
  const skip = page.getByRole("dialog").getByRole("button", { name: /^skip$/i });
  if (await skip.isVisible().catch(() => false)) await skip.click({ force: true });
}

test("household KB demo: admin shares → member finds + asks", async ({ browser }) => {
  test.skip(!ADMIN.email || !MEMBER.email, "Set admin (SMOKE) + member (DEMO_MEMBER) creds for the demo.");

  const newRecordedContext = () =>
    browser.newContext({
      viewport: { width: 1280, height: 800 },
      recordVideo: { dir: VIDEO_DIR, size: { width: 1280, height: 800 } },
    });

  // Record admin scenes, then CLOSE that context (flushes a tight admin video),
  // then record member scenes — so the two .webm files concatenate with no idle
  // footage. Scenes are sequential anyway (member reads what admin persisted).
  let sharedDocId: string | null = null;

  // ── Scenes 1–2 — Admin: upload → share ─────────────────────────────────────
  const adminCtx: BrowserContext = await newRecordedContext();
  const admin = await adminCtx.newPage();
  try {
    await loginAs(admin, ADMIN.email, ADMIN.password);
    await admin.goto("/documents");
    await admin.getByTestId("documents-page").waitFor({ timeout: 15_000 });
    await beat(admin);

    // Real upload → real ingest. Progress bar → indexed. (Pre-seed instead if you
    // want a shorter GIF: skip this scene and use an already-ingested doc id.)
    await admin.locator('[data-testid="document-upload-panel"] input[type="file"]').setInputFiles(SAMPLE_DOC);
    const row = admin.locator(`tr[data-document-id]`).filter({ hasText: SAMPLE_DOC_NAME }).first();
    await row.waitFor({ timeout: 120_000 });
    await beat(admin, 1500);
    sharedDocId = await row.getAttribute("data-document-id");

    // Scene 2 — share it with the household (the differentiating beat).
    await admin.goto(`/documents/${sharedDocId}`);
    await beat(admin);
    await admin.getByTestId("share-toggle").getByRole("switch").click(); // confirm auto-accepts (documents.spec.ts)
    await admin.getByText("Everyone in your household can find and read this.").waitFor({ timeout: 30_000 });
    await beat(admin, 1800); // hold on the "Shared" state
  } finally {
    await adminCtx.close(); // → VIDEO_DIR/<hash>.webm (admin scenes only)
  }

  // ── Scenes 3–4 — Member: search → ask ──────────────────────────────────────
  const memberCtx: BrowserContext = await newRecordedContext();
  const member = await memberCtx.newPage();
  try {
    await loginAs(member, MEMBER.email, MEMBER.password);
    await member.goto("/search");
    await beat(member);
    await humanType(member, () => member.getByPlaceholder("Search memories and entities…"), MEMBER_QUERY);
    await member.keyboard.press("Enter");
    // The money shot: the doc the ADMIN shared appears in the MEMBER's results.
    await member.getByText(SAMPLE_DOC_NAME).first().waitFor({ timeout: 30_000 });
    await beat(member, 1800);

    // Scene 4 — ask the document a question → grounded, cited answer.
    await member.goto(`/documents/${sharedDocId}/chat`);
    await beat(member);
    await humanType(member, () => member.getByPlaceholder("Ask about this document…"), CHAT_QUESTION);
    await member.getByRole("button", { name: "Send" }).click();
    await member.getByTestId("context-used-strip").waitFor({ timeout: 60_000 }); // citation proof
    await beat(member, 2500); // hold on the cited answer
  } finally {
    await memberCtx.close(); // → VIDEO_DIR/<hash>.webm (member scenes only)
  }
});
