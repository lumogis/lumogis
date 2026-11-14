// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-881 — the hand-written `ModelDisabledReason` list must equal the enum the
 * server publishes, so the Web cannot drift from `orchestrator/models/api_v1.py`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { MODEL_DISABLED_REASONS } from "../../src/api/models";

interface Schema {
  anyOf?: { enum?: string[]; type?: string }[];
}

describe("ModelDescriptor.disabled_reason contract", () => {
  it("TS vocabulary equals the OpenAPI snapshot enum", () => {
    const snapshot = JSON.parse(readFileSync(resolve(__dirname, "../../openapi.snapshot.json"), "utf8")) as {
      components: { schemas: Record<string, { properties: Record<string, Schema>; required?: string[] }> };
    };
    const descriptor = snapshot.components.schemas.ModelDescriptor;
    const prop = descriptor.properties.disabled_reason;
    expect(prop, "snapshot lacks ModelDescriptor.disabled_reason").toBeTruthy();
    const members = (prop.anyOf ?? []).filter((m) => Array.isArray(m.enum));
    expect(members).toHaveLength(1);
    expect(new Set(members[0].enum)).toEqual(new Set(MODEL_DISABLED_REASONS));
    expect(descriptor.required ?? []).not.toContain("disabled_reason");
  });
});
