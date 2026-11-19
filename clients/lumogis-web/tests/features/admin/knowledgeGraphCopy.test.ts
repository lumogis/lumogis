// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-937 — the Knowledge graph copy table. Headlines are written out here (not
 * imported), so a wrong state -> words mapping fails even if the module and its
 * test were edited together.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type {
  AdminKnowledgeGraphStatus,
  InstallKind,
  KnowledgeGraphState,
  ToggleFailedCode,
} from "../../../src/api/knowledgeGraph";
import { kgStatus } from "../../helpers/knowledgeGraphStatus";
import {
  ENABLE_ENV_LINES,
  formatGb,
  knowledgeGraphCopy,
  lowMemoryConfirmation,
  turnOffLabel,
  turnOnLabel,
  unhealthyReasonText,
} from "../../../src/features/admin/knowledgeGraphCopy";

const GIB = 1024 ** 3;

function status(
  state: KnowledgeGraphState,
  opts: {
    total?: number | null;
    enough?: boolean | null;
    reason?: string | null;
    install?: InstallKind;
    failed?: ToggleFailedCode | null;
  } = {},
): AdminKnowledgeGraphStatus {
  return kgStatus(state, opts);
}

const CASES: {
  state: KnowledgeGraphState;
  headline: string;
  tone: "ok" | "info" | "warn";
  steps: boolean;
  checkoutNote: boolean;
  diagnostics: boolean;
}[] = [
  { state: "on", headline: "The knowledge graph is on.", tone: "ok", steps: false, checkoutNote: false, diagnostics: false },
  { state: "starting", headline: "Starting the knowledge graph…", tone: "info", steps: false, checkoutNote: false, diagnostics: false },
  { state: "stopping", headline: "Stopping the knowledge graph…", tone: "info", steps: false, checkoutNote: false, diagnostics: false },
  { state: "start_failed", headline: "The knowledge graph did not start.", tone: "warn", steps: false, checkoutNote: false, diagnostics: false },
  { state: "stop_failed", headline: "The knowledge graph did not stop.", tone: "warn", steps: false, checkoutNote: false, diagnostics: false },
  {
    state: "service_unreachable",
    headline: "The knowledge graph is turned on, but Lumogis cannot reach the graph service.",
    tone: "warn",
    steps: false,
    checkoutNote: false,
    diagnostics: true,
  },
  {
    state: "service_wiring_failed",
    headline: "GRAPH_MODE is set to service, but Lumogis could not connect to the graph, so the graph is off.",
    tone: "warn",
    steps: false,
    checkoutNote: false,
    diagnostics: false,
  },
  {
    state: "unsupported_mode",
    headline: "GRAPH_MODE is set to inprocess, which does not work on the standard Lumogis image, so the graph is off.",
    tone: "warn",
    steps: true,
    checkoutNote: true,
    diagnostics: false,
  },
  { state: "off", headline: "The knowledge graph is off.", tone: "info", steps: true, checkoutNote: true, diagnostics: false },
  {
    state: "off_low_memory",
    headline: "The knowledge graph is off, and this machine does not have enough memory for it.",
    tone: "info",
    steps: false,
    checkoutNote: false,
    diagnostics: false,
  },
];

describe("knowledgeGraphCopy: one row per state", () => {
  for (const c of CASES) {
    it(`${c.state}`, () => {
      const copy = knowledgeGraphCopy(status(c.state));
      expect(copy.headline).toBe(c.headline);
      expect(copy.tone).toBe(c.tone);
      expect(copy.showSteps).toBe(c.steps);
      expect(copy.checkoutNote !== null).toBe(c.checkoutNote);
      expect(copy.showDiagnosticsLink).toBe(c.diagnostics);
    });
  }
});

describe("memoryLine", () => {
  it("says enough, with the total", () => {
    const line = knowledgeGraphCopy(status("off", { total: 16 * GIB, enough: true })).memoryLine;
    expect(line).toBe("This machine has about 16 GB of memory for Docker. That is enough.");
  });

  it("says not enough and points at Settings → Resources only then", () => {
    const low = knowledgeGraphCopy(
      status("off_low_memory", { total: Math.round(7.6 * GIB), enough: false }),
    ).memoryLine;
    expect(low).toContain("about 7.6 GB");
    expect(low).toContain("That is not enough");
    expect(low).toContain("about 10 GB in total");
    expect(low).toContain("Settings → Resources");
    const ok = knowledgeGraphCopy(status("off", { enough: true })).memoryLine;
    expect(ok).not.toContain("Settings → Resources");
  });

  it("says it could not read the memory when unknown", () => {
    const line = knowledgeGraphCopy(status("off", { total: null, enough: null })).memoryLine;
    expect(line).toBe(
      "Lumogis could not read how much memory this machine has. Check that it has about 10 GB before you turn the graph on.",
    );
  });

  it("is omitted when the graph is on", () => {
    expect(knowledgeGraphCopy(status("on")).memoryLine).toBeNull();
  });
});

describe("formatGb", () => {
  it("formats below and above 10 GB", () => {
    expect(formatGb(8_160_000_000)).toBe("7.6 GB");
    expect(formatGb(16 * GIB)).toBe("16 GB");
    expect(formatGb(12 * GIB)).toBe("12 GB");
  });
});

describe("unhealthyReasonText", () => {
  it("maps known codes to fixed text", () => {
    expect(unhealthyReasonText("timeout")).toBe("timed out");
    expect(unhealthyReasonText("connection_error")).toBe("could not connect");
    expect(unhealthyReasonText("network_error")).toBe("network error");
    expect(unhealthyReasonText("http_503")).toBe("answered HTTP 503");
  });

  it("never echoes an unknown code", () => {
    expect(unhealthyReasonText("<script>")).toBeNull();
    expect(unhealthyReasonText("weird")).toBeNull();
    expect(unhealthyReasonText(null)).toBeNull();
  });
});

describe("enable steps", () => {
  it("name GRAPH_MODE=service and match the docker-compose.premium.yml header", () => {
    expect(ENABLE_ENV_LINES).toContain("GRAPH_MODE=service");
    const compose = readFileSync(resolve(__dirname, "../../../../../docker-compose.premium.yml"), "utf8");
    for (const line of ENABLE_ENV_LINES) {
      if (line.startsWith("GRAPH_WEBHOOK_SECRET=")) {
        expect(compose).toContain("GRAPH_WEBHOOK_SECRET=");
        continue;
      }
      expect(compose, line).toContain(line);
    }
  });

  it("never offer GRAPH_MODE=inprocess in any state", () => {
    for (const c of CASES) {
      const copy = knowledgeGraphCopy(status(c.state, { reason: "timeout" }));
      const all = [copy.headline, ...copy.details, copy.adds, copy.needs, copy.memoryLine ?? ""].join("\n");
      expect(all).not.toContain("GRAPH_MODE=inprocess");
    }
    expect(ENABLE_ENV_LINES.join("\n")).not.toContain("inprocess");
  });
});

describe("service_unreachable details", () => {
  it("adds the last check when the reason is known", () => {
    const copy = knowledgeGraphCopy(status("service_unreachable", { reason: "timeout" }));
    expect(copy.details).toContain("Last check: timed out.");
  });

  it("has no Last check line when the reason is null or unknown", () => {
    for (const reason of [null, "weird"]) {
      const copy = knowledgeGraphCopy(status("service_unreachable", { reason }));
      expect(copy.details.some((d) => d.startsWith("Last check"))).toBe(false);
    }
  });
});


// ---------------------------------------------------------------------------
// LUM-989
// ---------------------------------------------------------------------------

describe("Release installs get a button, not steps", () => {
  it("off on a Release install: no steps, no checkout note, and the offer", () => {
    const copy = knowledgeGraphCopy(status("off", { install: "release" }));
    expect(copy.showSteps).toBe(false);
    expect(copy.checkoutNote).toBeNull();
    expect(copy.offer?.heading).toBe("Add the knowledge graph");
    expect(copy.offer?.text).toContain("about 1 GB");
  });

  it("the offer is absent when Turn on is not available, and on a checkout", () => {
    expect(knowledgeGraphCopy(status("off", { install: "checkout" })).offer).toBeNull();
    expect(knowledgeGraphCopy(kgStatus("off", { install: "release", turnOn: { available: false, block: "env_unwritable" } })).offer).toBeNull();
    expect(knowledgeGraphCopy(status("on", { install: "release" })).offer).toBeNull();
  });

  it("never says the graph cannot be turned on there yet", () => {
    for (const c of CASES) {
      for (const install of ["release", "checkout"] as const) {
        const copy = knowledgeGraphCopy(status(c.state, { install }));
        const all = [copy.headline, ...copy.details, copy.checkoutNote ?? "", copy.blockText ?? ""].join("\n");
        expect(all).not.toContain("cannot be turned on there yet");
      }
    }
  });
});

describe("start_failed and stop_failed copy per code", () => {
  const saved = "The setting is saved: a plain docker compose up -d would start the graph. Turn off to undo it.";

  it("start_failed carries the code's text and the saved-setting line", () => {
    const compose = knowledgeGraphCopy(status("start_failed", { failed: "compose_failed" })).details;
    expect(compose[0]).toContain("Docker could not start the graph");
    expect(compose).toContain(saved);
    expect(knowledgeGraphCopy(status("start_failed", { failed: "timeout" })).details[0]).toContain("took too long");
    expect(knowledgeGraphCopy(status("start_failed", { failed: "unreachable" })).details[0]).toContain("stack-control");
  });

  it("stop_failed carries the code's text and does not claim a saved start", () => {
    for (const code of ["compose_failed", "timeout", "unreachable"] as const) {
      const details = knowledgeGraphCopy(status("stop_failed", { failed: code })).details;
      expect(details).toHaveLength(1);
      expect(details).not.toContain(saved);
    }
    expect(knowledgeGraphCopy(status("stop_failed", { failed: "timeout" })).details[0]).toContain("took too long");
  });

  it("labels Retry on the failed side only", () => {
    expect(turnOnLabel(status("start_failed"))).toBe("Retry turn on");
    expect(turnOnLabel(status("off"))).toBe("Turn on");
    expect(turnOffLabel(status("stop_failed"))).toBe("Retry turn off");
    expect(turnOffLabel(status("on"))).toBe("Turn off");
  });
});

describe("block copy", () => {
  const cases: [string, string][] = [
    ["compose_too_old", "Download the docker-compose.yml of the release you run"],
    ["stack_control_unreachable", "stack-control"],
    ["env_unwritable", "cannot change its settings file"],
  ];
  for (const [block, text] of cases) {
    it(block, () => {
      const copy = knowledgeGraphCopy(
        kgStatus("off", { install: "release", turnOn: { available: false, block: block as never } }),
      );
      expect(copy.blockText).toContain(text);
    });
  }

  it("explains a missing Turn off while the graph is on (Turn on says already_on)", () => {
    const copy = knowledgeGraphCopy(
      kgStatus("on", {
        install: "release",
        turnOff: { available: false, block: "stack_control_unreachable" },
      }),
    );
    expect(copy.blockText).toContain("stack-control");
  });

  it("says nothing extra for already_on, in_progress and checkout", () => {
    expect(knowledgeGraphCopy(status("on", { install: "release" })).blockText).toBeNull();
    expect(knowledgeGraphCopy(status("starting", { install: "release" })).blockText).toBeNull();
    expect(knowledgeGraphCopy(status("off", { install: "checkout" })).blockText).toBeNull();
  });
});

describe("low-memory confirmation and the memory-led unreachable copy", () => {
  it("names Docker Desktop, the download and disk, and memory as the first thing to check", () => {
    const text = lowMemoryConfirmation(status("off_low_memory", { total: 8 * GIB, enough: false, install: "release" })).lines.join(" ");
    expect(text).toContain("about 8.0 GB");
    expect(text).toContain("about 10 GB in total");
    expect(text).toContain("Settings → Resources");
    expect(text).toContain("about 1 GB");
    expect(text).toContain("memory is the first thing to check");
  });

  it("service_unreachable leads with memory when the machine is short", () => {
    const short = knowledgeGraphCopy(status("service_unreachable", { enough: false })).details[0];
    expect(short).toContain("short of memory");
    const fine = knowledgeGraphCopy(status("service_unreachable", { enough: true })).details[0];
    expect(fine).not.toContain("short of memory");
  });

  it("the starting and stopping states have no memory line", () => {
    expect(knowledgeGraphCopy(status("starting")).memoryLine).toBeNull();
    expect(knowledgeGraphCopy(status("stopping")).memoryLine).toBeNull();
  });
});
