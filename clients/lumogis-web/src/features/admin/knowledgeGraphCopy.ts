// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-937 / LUM-989 — pure copy for the admin Knowledge graph page: no React, no
// fetching. The server computes `state` and which buttons exist; this module only
// turns them into words (same pattern as _shared/privacyBlockCopy.ts, ADR 227).
//
// Rule: `GRAPH_MODE=inprocess` never appears in a step or an offer. The word
// `inprocess` appears only in the `unsupported_mode` headline that names the
// current setting.

import type {
  AdminKnowledgeGraphStatus,
  KnowledgeGraphState,
  ToggleFailedCode,
  TurnOffBlock,
  TurnOnBlock,
} from "../../api/knowledgeGraph";

/** Public handbook anchor; must match the quickstart heading "Turn on the knowledge graph". */
export const KG_DOCS_URL =
  "https://github.com/lumogis/lumogis/blob/main/docs/deployment/quickstart.md#turn-on-the-knowledge-graph";

export const ADMIN_DIAGNOSTICS_PATH = "/settings/diagnostics";

/** Same lines as the header of docker-compose.premium.yml. */
export const ENABLE_ENV_LINES: string[] = [
  "COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml:docker-compose.premium.yml",
  "GRAPH_MODE=service",
  "KG_SERVICE_URL=http://lumogis-graph:8001",
  "GRAPH_WEBHOOK_SECRET=<a long random value>",
  "CAPABILITY_SERVICE_URLS=http://lumogis-graph:8001",
];

/** localStorage key for the dismissed System status offer line (one per device). */
export const GRAPH_OFFER_DISMISS_KEY = "lumogis:graph-offer-dismissed";

export type KnowledgeGraphTone = "ok" | "info" | "warn";

export interface KnowledgeGraphCopy {
  headline: string;
  tone: KnowledgeGraphTone;
  details: string[];
  adds: string;
  needs: string;
  memoryLine: string | null;
  showSteps: boolean;
  /** One line on a checkout: Release installs get a button instead of the steps. */
  checkoutNote: string | null;
  showDiagnosticsLink: boolean;
  /** Why a button is missing, when the server says it is blocked. */
  blockText: string | null;
  /** The admin can turn it on from here: the offer heading and text. */
  offer: { heading: string; text: string } | null;
}

const GIB = 1024 ** 3;

/** Binary gigabytes (matches the server): one decimal below 10 GB, whole number from 10 GB. */
export function formatGb(bytes: number): string {
  const gb = bytes / GIB;
  if (gb >= 10) return `${Math.round(gb)} GB`;
  return `${gb.toFixed(1)} GB`;
}

/** Known registry reason codes become fixed text; anything else is not echoed. */
export function unhealthyReasonText(reason: string | null): string | null {
  if (reason === null) return null;
  if (reason === "timeout") return "timed out";
  if (reason === "connection_error") return "could not connect";
  if (reason === "network_error") return "network error";
  const m = /^http_(\d{3})$/.exec(reason);
  if (m) return `answered HTTP ${m[1]}`;
  return null;
}

const HEADLINES: Record<KnowledgeGraphState, { headline: string; tone: KnowledgeGraphTone }> = {
  on: { headline: "The knowledge graph is on.", tone: "ok" },
  starting: { headline: "Starting the knowledge graph…", tone: "info" },
  stopping: { headline: "Stopping the knowledge graph…", tone: "info" },
  start_failed: { headline: "The knowledge graph did not start.", tone: "warn" },
  stop_failed: { headline: "The knowledge graph did not stop.", tone: "warn" },
  service_unreachable: {
    headline: "The knowledge graph is turned on, but Lumogis cannot reach the graph service.",
    tone: "warn",
  },
  service_wiring_failed: {
    headline:
      "GRAPH_MODE is set to service, but Lumogis could not connect to the graph, so the graph is off.",
    tone: "warn",
  },
  unsupported_mode: {
    headline:
      "GRAPH_MODE is set to inprocess, which does not work on the standard Lumogis image, so the graph is off.",
    tone: "warn",
  },
  off: { headline: "The knowledge graph is off.", tone: "info" },
  off_low_memory: {
    headline: "The knowledge graph is off, and this machine does not have enough memory for it.",
    tone: "info",
  },
};

const START_FAILED_TEXT: Record<ToggleFailedCode, string> = {
  compose_failed:
    "Docker could not start the graph, for example because the download failed or the disk is full. Check your internet connection and free disk space, then retry.",
  timeout:
    "Starting took too long. The first start downloads about 1 GB, so a slow connection can run out the time. Retry: the download carries on where it stopped.",
  unreachable:
    "Lumogis could not reach its helper (the stack-control container) to start the graph. Check that it is running (docker compose ps), then retry.",
};

const STOP_FAILED_TEXT: Record<ToggleFailedCode, string> = {
  compose_failed: "Docker could not stop the graph. Retry, or check docker compose ps.",
  timeout: "Stopping took too long. Retry.",
  unreachable:
    "Lumogis could not reach its helper (the stack-control container) to stop the graph. Check that it is running (docker compose ps), then retry.",
};

const START_FAILED_SAVED =
  "The setting is saved: a plain docker compose up -d would start the graph. Turn off to undo it.";

/** Fixed text per block code; the server's `turn_on` / `turn_off` decide when it shows. */
const BLOCK_TEXT: Partial<Record<TurnOnBlock | TurnOffBlock, string>> = {
  compose_too_old:
    "This docker-compose.yml is older than your Lumogis and does not include the knowledge graph. Download the docker-compose.yml of the release you run (keep your .env), then run docker compose up -d and reload this page.",
  stack_control_unreachable:
    "Lumogis cannot reach its helper (the stack-control container), so it cannot turn the graph on or off from here. Check that it is running (docker compose ps).",
  env_unwritable:
    "Lumogis cannot change its settings file (.env), so it cannot turn the graph on or off from here.",
};

export function blockTextFor(status: AdminKnowledgeGraphStatus): string | null {
  // The buttons' own state words ("already on", "in progress") are the headline and
  // have no text here, so look at both buttons and take the first block with words:
  // with the graph on, Turn on says already_on and the reason Turn off is missing is
  // on the other button.
  for (const block of [status.turn_on.block, status.turn_off.block]) {
    const text = block ? BLOCK_TEXT[block] : undefined;
    if (text) return text;
  }
  return null;
}

/** The "I understand" confirmation shown before a Turn on that the memory check calls short. */
export function lowMemoryConfirmation(status: AdminKnowledgeGraphStatus): {
  lines: string[];
  checkbox: string;
} {
  const { total_bytes: total, required_bytes: required } = status.memory;
  const have = total !== null ? `about ${formatGb(total)}` : "less than that";
  return {
    lines: [
      `This machine has ${have} of memory for Docker and the graph needs about ${formatGb(required)} in total.`,
      "It can still run, but Lumogis may become slow or the graph may keep stopping. If that happens, memory is the first thing to check, and Turn off undoes this.",
      "If you use Docker Desktop, you can raise its memory under Settings → Resources.",
      "The first start downloads about 1 GB, so check that you have the free disk space.",
    ],
    checkbox: "I understand, turn it on anyway",
  };
}

export const TURN_OFF_CONFIRMATION =
  "Turn off the knowledge graph? Lumogis restarts for a moment. Your graph data is kept, so turning it on again picks up where it left off.";

export const TURN_ON_NOTE =
  "The first start downloads about 1 GB and takes a few minutes. Lumogis restarts for a moment.";

export const OFFER_LINE_TEXT =
  "You can add the knowledge graph: it links the people, projects and documents in your library.";

function detailsFor(status: AdminKnowledgeGraphStatus): string[] {
  switch (status.state) {
    case "on":
      return [
        "Lumogis is connected to the graph service and adds new documents and chats to the graph.",
      ];
    case "starting":
      return [
        "The first start downloads about 1 GB, so it can take several minutes. You can leave this page: it says so here when the graph is on. Lumogis restarts for a moment.",
      ];
    case "stopping":
      return ["Lumogis restarts for a moment. Your graph data is kept."];
    case "start_failed": {
      const code = status.failed_code ?? "compose_failed";
      return [START_FAILED_TEXT[code], START_FAILED_SAVED];
    }
    case "stop_failed":
      return [STOP_FAILED_TEXT[status.failed_code ?? "compose_failed"]];
    case "service_unreachable": {
      // A confirmed low-memory start often ends here: the graph is killed for lack of memory.
      if (status.memory.enough === false) {
        const lines = [
          "This machine is short of memory for the graph, which is the most likely reason it keeps stopping. Turn off, or give Docker more memory (Docker Desktop: Settings → Resources) and turn it on again.",
        ];
        const reason = unhealthyReasonText(status.service.last_unhealthy_reason);
        if (reason !== null) lines.push(`Last check: ${reason}.`);
        return lines;
      }
      const lines = [
        "Check that the lumogis-graph and falkordb containers are running (docker compose ps) and that .env has CAPABILITY_SERVICE_URLS=http://lumogis-graph:8001. Right after a restart this can take up to five minutes to clear. If it stays like this, Turn off and then Turn on again.",
      ];
      const reason = unhealthyReasonText(status.service.last_unhealthy_reason);
      if (reason !== null) lines.push(`Last check: ${reason}.`);
      return lines;
    }
    case "service_wiring_failed":
      return ["The orchestrator log has a graph_mode_fallback warning with the reason."];
    case "unsupported_mode":
      return ["Only GRAPH_MODE=service is supported. Follow the steps below to switch."];
    default:
      return [];
  }
}

function memoryLineFor(status: AdminKnowledgeGraphStatus): string | null {
  if (status.state === "on" || status.state === "starting" || status.state === "stopping") {
    return null;
  }
  const { total_bytes: total, required_bytes: required, enough } = status.memory;
  const requiredText = formatGb(required);
  if (enough === true && total !== null) {
    return `This machine has about ${formatGb(total)} of memory for Docker. That is enough.`;
  }
  if (enough === false && total !== null) {
    return `This machine has about ${formatGb(total)} of memory for Docker. That is not enough: the graph needs about ${requiredText} in total. If you use Docker Desktop, this is the memory Docker Desktop may use; you can raise it under Settings → Resources.`;
  }
  return `Lumogis could not read how much memory this machine has. Check that it has about ${requiredText} before you turn the graph on.`;
}

export function knowledgeGraphCopy(status: AdminKnowledgeGraphStatus): KnowledgeGraphCopy {
  const { headline, tone } = HEADLINES[status.state];
  const requiredText = formatGb(status.memory.required_bytes);
  const release = status.install_kind === "release";
  // The written steps are for a git checkout, where the graph image is built locally.
  const offerable = status.state === "off" || status.state === "unsupported_mode";
  const canOffer = status.state === "off" && status.turn_on.available;
  return {
    headline,
    tone,
    details: detailsFor(status),
    adds: "Lumogis learns the people, places, organisations and projects in your documents and chats whether the graph is on or off, and search and chat work without it. The graph adds the links between them, so chat can use how people, projects and documents connect as context.",
    needs: `Two more containers: FalkorDB (the graph database) and the Lumogis graph service. Together they need about 2 GB more memory. Lumogis itself needs about 8 GB, so plan on about ${requiredText} in total.`,
    memoryLine: memoryLineFor(status),
    showSteps: offerable && !release,
    checkoutNote:
      offerable && !release
        ? "Installed from the two Release files (docker-compose.yml and example.env)? Then this page has a Turn on button instead of these steps."
        : null,
    showDiagnosticsLink: status.state === "service_unreachable",
    blockText: blockTextFor(status),
    offer: canOffer
      ? {
          heading: "Add the knowledge graph",
          text: `${OFFER_LINE_TEXT} ${TURN_ON_NOTE}`,
        }
      : null,
  };
}

/** Button labels: a failed start or stop is retried with the same button. */
export function turnOnLabel(status: AdminKnowledgeGraphStatus): string {
  return status.state === "start_failed" ? "Retry turn on" : "Turn on";
}

export function turnOffLabel(status: AdminKnowledgeGraphStatus): string {
  return status.state === "stop_failed" ? "Retry turn off" : "Turn off";
}
