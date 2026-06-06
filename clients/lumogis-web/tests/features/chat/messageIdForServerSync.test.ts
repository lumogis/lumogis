// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it } from "vitest";

import { messageIdForServerSync } from "../../../src/features/chat/messageIdForServerSync";

describe("messageIdForServerSync", () => {
  it("strips u_/a_ prefixes so the API receives a bare UUID", () => {
    const bare = "550e8400-e29b-41d4-a716-446655440000";
    expect(messageIdForServerSync(`u_${bare}`)).toBe(bare);
    expect(messageIdForServerSync(`a_${bare}`)).toBe(bare);
  });

  it("passes through an already-bare UUID", () => {
    const bare = "00000000-0000-4000-8000-000000000099";
    expect(messageIdForServerSync(bare)).toBe(bare);
  });
});
