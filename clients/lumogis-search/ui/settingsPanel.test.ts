// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  composeSettingsBody,
  defaultClientSettingsDescriptor,
  GOLDEN_ADMIN_COMPOSE_OPTS,
  GOLDEN_MEMBER_COMPOSE_OPTS,
  ingestHostLabel,
  overlaySettingsSavePayload,
  settingsPathsGuideMarkup,
} from "./settingsPanel";

const fixturesDir = join(dirname(fileURLToPath(import.meta.url)), "fixtures");

describe("settingsPanel", () => {
  it("server_profile_ingest_host_label", () => {
    expect(ingestHostLabel("server")).toBe("this server");
    expect(settingsPathsGuideMarkup("server", true, true)).toContain(
      "folders on <em>this server</em>",
    );
  });

  it("default_descriptor_ingest_heading_exact", () => {
    const d = defaultClientSettingsDescriptor(true, true, "client-only");
    expect(d.ingestLabels.heading).toBe(
      "Server ingest paths (indexing on the server running Lumogis Core)",
    );
  });

  it("default_descriptor_member_no_admin_ingest", () => {
    const d = defaultClientSettingsDescriptor(false, true, "client-only");
    expect(d.showPushUpload).toBe(true);
    expect(d.pathsGuideHtml).not.toContain("Server ingest paths</strong> — folders on");
    expect(d.pathsGuideHtml).toContain("Push upload");
  });

  it("compose_settings_body_client_admin_golden", () => {
    const html = composeSettingsBody(GOLDEN_ADMIN_COMPOSE_OPTS);
    const baseline = readFileSync(
      join(fixturesDir, "settings-panel-baseline-admin.html"),
      "utf8",
    );
    expect(html).toBe(baseline);
  });

  it("compose_settings_body_client_member_golden", () => {
    const html = composeSettingsBody(GOLDEN_MEMBER_COMPOSE_OPTS);
    const baseline = readFileSync(
      join(fixturesDir, "settings-panel-baseline-member.html"),
      "utf8",
    );
    expect(html).toBe(baseline);
  });

  it("save_all_settings_bundled_preserves_hidden_fields", () => {
    const bundledDescriptor = {
      ...defaultClientSettingsDescriptor(true, false, "bundled"),
      showOrchestratorUrl: false,
      showLibraryRoots: false,
      showPushUpload: false,
    };
    const settings = {
      orchestratorBaseUrl: "http://127.0.0.1:58000",
      libraryRoots: ["/var/lib/lumogis/library"],
    };
    const payload = overlaySettingsSavePayload(bundledDescriptor, settings, {
      hotkey: "CommandOrControl+Shift+L",
    }, "system");
    expect(payload.orchestratorBaseUrl).toBe("http://127.0.0.1:58000");
    expect(payload.libraryRoots).toEqual(["/var/lib/lumogis/library"]);
  });

  it("client_only_still_shows_library_roots", () => {
    const d = defaultClientSettingsDescriptor(false, true, "client-only");
    expect(d.showLibraryRoots).toBe(true);
  });
});
