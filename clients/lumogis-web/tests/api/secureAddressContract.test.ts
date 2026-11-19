// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-331 — the hand-written state, error and warning lists must equal the
 * enums the server publishes (`orchestrator/models/api_v1.py`), so the copy
 * module can never miss a code the server sends.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  SECURE_ADDRESS_ERROR_CODES,
  SECURE_ADDRESS_STATES,
  SECURE_ADDRESS_WARNINGS,
  type SecureAddressStatus,
} from "../../src/api/secureAddress";

interface Prop {
  enum?: string[];
  anyOf?: Prop[];
  items?: Prop;
}

const schemas = (
  JSON.parse(readFileSync(resolve(__dirname, "../../openapi.snapshot.json"), "utf8")) as {
    components: { schemas: Record<string, { properties: Record<string, Prop> }> };
  }
).components.schemas;

/** Inline enum, the enum inside an Optional's anyOf, or an array's item enum. */
function enumOf(prop: Prop): string[] {
  if (prop.enum) return prop.enum;
  if (prop.items) return enumOf(prop.items);
  for (const p of prop.anyOf ?? []) if (p.enum) return p.enum;
  return [];
}

describe("SecureAddressStatus contract", () => {
  const props = schemas.SecureAddressStatus.properties;

  it("states equal the snapshot enum (status and stack-status summary)", () => {
    expect(new Set(SECURE_ADDRESS_STATES)).toEqual(new Set(enumOf(props.state)));
    expect(new Set(SECURE_ADDRESS_STATES)).toEqual(
      new Set(enumOf(schemas.SecureAddressSummary.properties.state)),
    );
  });

  it("error codes equal the snapshot enum", () => {
    expect(new Set(SECURE_ADDRESS_ERROR_CODES)).toEqual(new Set(enumOf(props.error_code)));
  });

  it("warnings equal the snapshot enum", () => {
    expect(new Set(SECURE_ADDRESS_WARNINGS)).toEqual(new Set(enumOf(props.warnings)));
  });

  it("every key of the TS interface is a snapshot property, and name_new_until is one", () => {
    // Typed as the full interface, so a field added to it must be added here too (build fails).
    const fixture: Record<keyof SecureAddressStatus, true> = {
      state: true,
      name: true,
      url: true,
      lan_address: true,
      lan_address_source: true,
      suggested_address: true,
      detection_unavailable_reason: true,
      certificate_valid_until: true,
      resolver: true,
      error_code: true,
      retry_after_seconds: true,
      name_changed: true,
      name_new_until: true,
      sign_in_enabled: true,
      warnings: true,
    };
    expect(Object.keys(props)).toContain("name_new_until");
    const snapshotKeys = new Set(Object.keys(props));
    for (const key of Object.keys(fixture)) expect(snapshotKeys.has(key), key).toBe(true);
    for (const key of snapshotKeys) expect(key in fixture, key).toBe(true);
  });

  it("the bearer is never part of the response", () => {
    expect(Object.keys(props)).not.toContain("bearer");
  });
});
