// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// A complete admin knowledge-graph status for tests (LUM-989). The server
// computes `turn_on` / `turn_off`; this helper mirrors the plan's availability
// table so a test can say "the server said X" in one line. The Web never derives it.

import type {
  AdminKnowledgeGraphStatus,
  AdminKnowledgeGraphTurnOff,
  AdminKnowledgeGraphTurnOn,
  InstallKind,
  KnowledgeGraphState,
  ToggleFailedCode,
} from "../../src/api/knowledgeGraph";

export const GIB = 1024 ** 3;

export interface StatusOpts {
  install?: InstallKind;
  total?: number | null;
  enough?: boolean | null;
  reason?: string | null;
  failed?: ToggleFailedCode | null;
  turnOn?: Partial<AdminKnowledgeGraphTurnOn>;
  turnOff?: Partial<AdminKnowledgeGraphTurnOff>;
}

function availability(
  state: KnowledgeGraphState,
  install: InstallKind,
  enough: boolean | null,
): { on: AdminKnowledgeGraphTurnOn; off: AdminKnowledgeGraphTurnOff } {
  if (install !== "release") {
    return {
      on: { available: false, block: "checkout", needs_confirmation: false },
      off: { available: false, block: "checkout" },
    };
  }
  const transient = state === "starting" || state === "stopping";
  const alreadyOn = state === "on" || state === "service_unreachable" || state === "service_wiring_failed";
  const alreadyOff = state === "off" || state === "off_low_memory";
  const on: AdminKnowledgeGraphTurnOn = transient
    ? { available: false, block: "in_progress", needs_confirmation: false }
    : alreadyOn
      ? { available: false, block: "already_on", needs_confirmation: false }
      : state === "stop_failed" || state === "unsupported_mode"
        ? { available: false, block: null, needs_confirmation: false }
        : { available: true, block: null, needs_confirmation: enough === false };
  const off: AdminKnowledgeGraphTurnOff = transient
    ? { available: false, block: "in_progress" }
    : alreadyOff
      ? { available: false, block: "already_off" }
      : state === "unsupported_mode"
        ? { available: false, block: null }
        : { available: true, block: null };
  return { on, off };
}

export function kgStatus(state: KnowledgeGraphState, opts: StatusOpts = {}): AdminKnowledgeGraphStatus {
  const install = opts.install ?? "checkout";
  const total = opts.total === undefined ? 16 * GIB : opts.total;
  const enough = opts.enough === undefined ? (state === "off_low_memory" ? false : true) : opts.enough;
  const live = state === "on" || state === "service_unreachable" || state === "stopping";
  const { on, off } = availability(state, install, enough);
  return {
    state,
    mode: live ? "service" : "disabled",
    configured_mode: live ? "service" : "disabled",
    service: {
      registered: state === "on" || state === "stopping",
      healthy: state === "on" || state === "stopping" ? true : state === "service_unreachable" ? false : null,
      last_seen_healthy: null,
      last_unhealthy_reason:
        opts.reason !== undefined ? opts.reason : state === "service_unreachable" ? "timeout" : null,
    },
    memory: { total_bytes: total, required_bytes: 10 * GIB, enough },
    install_kind: install,
    turn_on: { ...on, ...opts.turnOn },
    turn_off: { ...off, ...opts.turnOff },
    failed_code:
      opts.failed !== undefined ? opts.failed : state === "start_failed" || state === "stop_failed" ? "compose_failed" : null,
  };
}
