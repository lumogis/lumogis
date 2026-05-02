// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * IndexedDB draft store (Phase 3C): text-only, key shape, truncation, empty-delete.
 *
 * Requires `fake-indexeddb/auto` loaded from tests/setup.ts.
 */
import { clear } from "idb-keyval";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";

import {
  deleteDraft,
  DRAFT_MAX_CHARS,
  getDraft,
  makeCaptureDraftKey,
  makeChatDraftKey,
  setDraft,
} from "../../src/pwa/drafts";

const draftsPath = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "src",
  "pwa",
  "drafts.ts",
);

describe("pwa drafts (idb-keyval)", () => {
  beforeEach(async () => {
    await clear();
  });

  it("makeChatDraftKey is stable per thread id", () => {
    expect(makeChatDraftKey("abc")).toBe("lumogis:draft:chat:abc");
    expect(makeChatDraftKey("thread-99")).toBe("lumogis:draft:chat:thread-99");
  });

  it("makeCaptureDraftKey is stable per capture id (Phase 5 reserved)", () => {
    expect(makeCaptureDraftKey("cap-1")).toBe("lumogis:draft:capture:cap-1");
  });

  it("setDraft persists non-empty text; getDraft round-trips", async () => {
    const key = makeChatDraftKey("t1");
    await setDraft(key, "hello");
    expect(await getDraft(key)).toBe("hello");
  });

  it("setDraft deletes on whitespace-only", async () => {
    const key = makeChatDraftKey("t2");
    await setDraft(key, "x");
    expect(await getDraft(key)).toBe("x");
    await setDraft(key, "   ");
    expect(await getDraft(key)).toBeUndefined();
  });

  it("deleteDraft removes entries", async () => {
    const key = makeChatDraftKey("t3");
    await setDraft(key, "d");
    await deleteDraft(key);
    expect(await getDraft(key)).toBeUndefined();
  });

  it("truncates drafts longer than DRAFT_MAX_CHARS", async () => {
    const key = makeChatDraftKey("t-big");
    const huge = "a".repeat(DRAFT_MAX_CHARS + 500);
    await setDraft(key, huge);
    const got = await getDraft(key);
    expect(got?.length).toBe(DRAFT_MAX_CHARS);
    expect(got?.at(0)).toBe("a");
  });

  it("draft module does not import auth, tokens, api client, or service worker tooling", () => {
    const draftsSource = readFileSync(draftsPath, "utf8");
    expect(draftsSource).not.toMatch(/tokens|Authorization|credential|refresh|serviceWorker/i);
    expect(draftsSource).not.toMatch(/ApiClient|"\.\.\/api\//);
  });
});
