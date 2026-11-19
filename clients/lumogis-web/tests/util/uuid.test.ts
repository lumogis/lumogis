// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, describe, expect, it, vi } from "vitest";

import { randomUuid } from "../../src/util/uuid";

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("randomUuid", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a version 4 UUID in a secure context", () => {
    expect(randomUuid()).toMatch(V4);
  });

  it("works on a plain-HTTP page, where randomUUID is missing", () => {
    const real = globalThis.crypto;
    vi.stubGlobal("crypto", { getRandomValues: real.getRandomValues.bind(real) });
    const ids = new Set(Array.from({ length: 200 }, () => randomUuid()));
    for (const id of ids) expect(id).toMatch(V4);
    expect(ids.size).toBe(200);
  });

  it("falls back when randomUUID throws instead of being missing", () => {
    const real = globalThis.crypto;
    vi.stubGlobal("crypto", {
      randomUUID: () => {
        throw new Error("insecure context");
      },
      getRandomValues: real.getRandomValues.bind(real),
    });
    expect(randomUuid()).toMatch(V4);
  });
});
