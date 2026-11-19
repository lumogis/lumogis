// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-937 — the hand-written state and mode lists must equal the enums the
 * server publishes, so the Web cannot drift from `orchestrator/models/api_v1.py`.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  GRAPH_MODE_TOKENS,
  KNOWLEDGE_GRAPH_STATES,
  TOGGLE_FAILED_CODES,
  TURN_OFF_BLOCKS,
  TURN_ON_BLOCKS,
} from "../../src/api/knowledgeGraph";

interface Prop {
  enum?: string[];
  $ref?: string;
}
interface Schema {
  properties: Record<string, Prop>;
  required?: string[];
  enum?: string[];
}

describe("AdminKnowledgeGraphStatus contract", () => {
  const snapshot = JSON.parse(readFileSync(resolve(__dirname, "../../openapi.snapshot.json"), "utf8")) as {
    components: { schemas: Record<string, Schema> };
  };
  const schema = snapshot.components.schemas.AdminKnowledgeGraphStatus;

  /** A required Literal is an inline `enum`; resolve a `$ref` if the generator ever hoists it. */
  function enumOf(prop: Prop): string[] {
    if (prop.enum) return prop.enum;
    if (prop.$ref) {
      const name = prop.$ref.split("/").pop() as string;
      return snapshot.components.schemas[name].enum ?? [];
    }
    return [];
  }

  it("the snapshot has the schema", () => {
    expect(schema, "snapshot lacks AdminKnowledgeGraphStatus").toBeTruthy();
  });

  it("TS states equal the snapshot enum", () => {
    expect(new Set(KNOWLEDGE_GRAPH_STATES)).toEqual(new Set(enumOf(schema.properties.state)));
  });

  it("TS mode tokens equal the enum of mode and configured_mode", () => {
    expect(new Set(GRAPH_MODE_TOKENS)).toEqual(new Set(enumOf(schema.properties.mode)));
    expect(new Set(GRAPH_MODE_TOKENS)).toEqual(new Set(enumOf(schema.properties.configured_mode)));
  });

  it("state, mode and configured_mode are required", () => {
    expect(schema.required ?? []).toEqual(expect.arrayContaining(["state", "mode", "configured_mode"]));
  });

  it("LUM-989: the status carries the install kind, both buttons and the failure code", () => {
    expect(Object.keys(schema.properties)).toEqual(
      expect.arrayContaining(["install_kind", "turn_on", "turn_off", "failed_code"]),
    );
    expect(schema.required ?? []).toEqual(
      expect.arrayContaining(["install_kind", "turn_on", "turn_off"]),
    );
    expect(new Set(enumOf(schema.properties.install_kind))).toEqual(
      new Set(["release", "checkout", "unknown"]),
    );
  });

  it("LUM-989: the new state names are the snapshot's", () => {
    const states = new Set(enumOf(schema.properties.state));
    for (const s of ["starting", "stopping", "start_failed", "stop_failed"]) {
      expect(states.has(s), s).toBe(true);
    }
  });

  /** Optional (`default=None`) fields come out as `anyOf`/`$ref`; find the enum behind them. */
  function deepEnum(prop: unknown): string[] {
    const out: string[] = [];
    const walk = (n: unknown): void => {
      if (!n || typeof n !== "object") return;
      const o = n as Record<string, unknown>;
      if (Array.isArray(o.enum)) out.push(...(o.enum as string[]).filter((x) => typeof x === "string"));
      if (typeof o.$ref === "string") {
        walk(snapshot.components.schemas[(o.$ref as string).split("/").pop() as string]);
      }
      for (const k of ["anyOf", "oneOf", "allOf"]) {
        if (Array.isArray(o[k])) (o[k] as unknown[]).forEach(walk);
      }
    };
    walk(prop);
    return out;
  }

  it("LUM-989: failure codes and both block lists equal the snapshot's", () => {
    expect(new Set(TOGGLE_FAILED_CODES)).toEqual(new Set(deepEnum(schema.properties.failed_code)));
    const on = snapshot.components.schemas.AdminKnowledgeGraphTurnOn;
    const off = snapshot.components.schemas.AdminKnowledgeGraphTurnOff;
    expect(new Set(TURN_ON_BLOCKS)).toEqual(new Set(deepEnum(on.properties.block)));
    expect(new Set(TURN_OFF_BLOCKS)).toEqual(new Set(deepEnum(off.properties.block)));
  });

  it("LUM-989: the toggle request takes one boolean and nothing else", () => {
    const req = snapshot.components.schemas.AdminKnowledgeGraphToggleRequest as unknown as {
      properties: Record<string, unknown>;
      additionalProperties?: boolean;
    };
    expect(Object.keys(req.properties)).toEqual(["confirm_low_memory"]);
    expect(req.additionalProperties).toBe(false);
  });
});
