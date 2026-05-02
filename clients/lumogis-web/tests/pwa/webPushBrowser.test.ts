// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Unit tests — base64url decode for Web Push (`urlBase64ToUint8Array`).

import { describe, expect, it } from "vitest";

import { urlBase64ToUint8Array } from "../../src/pwa/webPushBrowser";

describe("webPushBrowser", () => {
  it("urlBase64ToUint8Array decodes padded base64 URL string", () => {
    /** "foobar" RFC 3548-safe */
    const b64url = "Zm9vYmFy";
    const u8 = urlBase64ToUint8Array(b64url);
    expect(Array.from(u8)).toEqual(["f".charCodeAt(0), "o".charCodeAt(0), "o".charCodeAt(0), "b".charCodeAt(0), "a".charCodeAt(0), "r".charCodeAt(0)]);
  });

  it("urlBase64ToUint8Array handles unpadded lengths", () => {
    const u8 = urlBase64ToUint8Array("AQI");
    expect(u8.length).toBe(2);
  });
});
