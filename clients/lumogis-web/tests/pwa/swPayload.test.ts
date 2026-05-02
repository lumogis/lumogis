// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Phase 4D — pure payload + click URL sanitization (bundled into `dist/sw.js` via `swPush.ts`).
 */
import {describe, expect, it} from "vitest";

import {
  normalizePushPayloadFromJson,
  NOTIFICATION_ALLOWED_PATHS,
  PUSH_FALLBACK_BODY,
  PUSH_FALLBACK_TAG,
  PUSH_FALLBACK_TITLE,
  sanitizeNotificationClickUrl,
} from "../../src/pwa/swPush";

describe("sanitizeNotificationClickUrl", () => {
  it("allows only exact allowlisted relative paths without query/hash", () => {
    for (const p of NOTIFICATION_ALLOWED_PATHS) {
      expect(sanitizeNotificationClickUrl(p)).toBe(p);
    }
    expect(sanitizeNotificationClickUrl("/chat/")).toBe("/chat");
    expect(sanitizeNotificationClickUrl("/approvals")).toBe("/approvals");
  });

  it("falls back to / for external, scheme, protocol-relative, or non-root-relative values", () => {
    expect(sanitizeNotificationClickUrl("https://evil.test/x")).toBe("/");
    expect(sanitizeNotificationClickUrl("//evil.test/x")).toBe("/");
    expect(sanitizeNotificationClickUrl("javascript:alert(1)")).toBe("/");
    expect(sanitizeNotificationClickUrl("data:text/html,")).toBe("/");
    expect(sanitizeNotificationClickUrl("relative-no-slash")).toBe("/");
  });

  it("strips query and hash; path stays allowlisted", () => {
    expect(sanitizeNotificationClickUrl("/approvals?utm=h")).toBe("/approvals");
    expect(sanitizeNotificationClickUrl("/chat#frag")).toBe("/chat");
  });

  it("rejects disallowed paths", () => {
    expect(sanitizeNotificationClickUrl("/settings")).toBe("/");
    expect(sanitizeNotificationClickUrl("/me")).toBe("/");
  });

  it("handles nullish", () => {
    expect(sanitizeNotificationClickUrl(undefined)).toBe("/");
    expect(sanitizeNotificationClickUrl(null)).toBe("/");
  });
});

describe("normalizePushPayloadFromJson", () => {
  it("preserves backend-shaped payload", () => {
    const o = normalizePushPayloadFromJson({
      title: "Lumogis",
      body: "Approval required",
      url: "/approvals",
    });
    expect(o.title).toBe("Lumogis");
    expect(o.body).toBe("Approval required");
    expect(o.tag).toBe(PUSH_FALLBACK_TAG);
    expect(o.targetPath).toBe("/approvals");
  });

  it("uses defaults for missing or malformed input", () => {
    const d = normalizePushPayloadFromJson(undefined);
    expect(d.title).toBe(PUSH_FALLBACK_TITLE);
    expect(d.body).toBe(PUSH_FALLBACK_BODY);
    expect(d.tag).toBe(PUSH_FALLBACK_TAG);
    expect(d.targetPath).toBe("/");

    expect(normalizePushPayloadFromJson(null).targetPath).toBe("/");
    expect(normalizePushPayloadFromJson("nope").title).toBe(PUSH_FALLBACK_TITLE);
    expect(normalizePushPayloadFromJson([]).targetPath).toBe("/");

    expect(
      normalizePushPayloadFromJson({
        malformed: {},
        extra: [1, 2, 3],
      }).tag,
    ).toBe(PUSH_FALLBACK_TAG);
  });

  it("ignores unknown fields", () => {
    const o = normalizePushPayloadFromJson({
      title: "Hi",
      body: "There",
      url: "/chat",
      secret: "x",
      nested: {a: 1},
    } as Record<string, unknown>);
    expect(o.title).toBe("Hi");
    expect(o.body).toBe("There");
    expect(o.targetPath).toBe("/chat");
    expect(JSON.stringify(o)).not.toContain("secret");
  });

  it("caps title and body length", () => {
    const long = "x".repeat(500);
    const o = normalizePushPayloadFromJson({title: long, body: long, url: "/"});
    expect(o.title.length).toBeLessThanOrEqual(120);
    expect(o.body.length).toBeLessThanOrEqual(240);
  });

  it("sanitizes url through same policy as clicks", () => {
    expect(normalizePushPayloadFromJson({title: "a", body: "b", url: "https://x.test/"}).targetPath).toBe("/");
    expect(normalizePushPayloadFromJson({title: "a", body: "b", url: "/approvals"}).targetPath).toBe("/approvals");
  });

  it("supports optional tag string", () => {
    expect(normalizePushPayloadFromJson({title: "T", body: "B", url: "/", tag: "lumogis-approval"}).tag).toBe(
      "lumogis-approval",
    );
    expect(normalizePushPayloadFromJson({title: "T", body: "B", url: "/", tag: "x".repeat(200)}).tag.length).toBeLessThanOrEqual(
      64,
    );
  });
});
