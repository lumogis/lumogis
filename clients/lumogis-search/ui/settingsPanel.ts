// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { iconMarkup } from "./primitives";
import type { ThemeMode } from "./theme";
import type { AuthMode } from "./overlayUi";

export type DesktopProfile = "client-only" | "bundled" | "server";

export type IngestSectionLabels = {
  heading: string;
  hint: string;
  restartBannerText: string;
  restartConfirmText: string;
  saveSuccessHint: string;
};

export type SearchCopy = {
  placeholder: string;
  loginHint: string;
  emptyHint: string;
  rootsBanner?: string;
};

export type SettingsPanelDescriptor = {
  pathsGuideHtml: string;
  showOrchestratorUrl: boolean;
  showLibraryRoots: boolean;
  showPushUpload: boolean;
  ingestLabels: IngestSectionLabels;
  searchCopy: SearchCopy;
};

export type OverlaySettingsSaveInput = {
  orchestratorBaseUrl: string;
  hotkey: string;
  libraryRoots: string[];
  theme: ThemeMode;
};

export type ComposeSettingsBodyOptions = {
  descriptor: SettingsPanelDescriptor;
  themeMode: ThemeMode;
  showAdminIngest: boolean;
  showPickLibraryFolder: boolean;
  needsLogin: boolean;
  authMode: AuthMode;
  sessionPresent: boolean;
  ingestPaths: string[];
  paperlessConfigured: boolean;
  restartRequired: boolean;
  overlayStatusPillHtml: string;
};

function escapeHtml(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function ingestHostLabel(profile: DesktopProfile): string {
  if (profile === "bundled") return "this Hub";
  if (profile === "server") return "this server";
  return "the server running Lumogis Core";
}

export function settingsPathsGuideMarkup(
  profile: DesktopProfile,
  showAdminIngest: boolean,
  showUpload: boolean,
): string {
  const coreHost =
    profile === "bundled"
      ? "this Hub"
      : profile === "server"
        ? "this server"
        : "your Lumogis server";
  const lines = [
    `<p class="hint"><strong>Library roots</strong> — folders on <em>this computer</em>. Search hits use them for Open / Shift+click reveal only. They do not control indexing.</p>`,
  ];
  if (showAdminIngest) {
    lines.push(
      `<p class="hint"><strong>Server ingest paths</strong> — folders on <em>${coreHost}</em> (operator). Lumogis watches these and builds household memory. Not the same as library roots.</p>`,
    );
  }
  if (showUpload) {
    lines.push(
      `<p class="hint"><strong>Push upload</strong> — send one file from this computer to your personal queue on the server, without adding an ingest folder.</p>`,
    );
  }
  return `<aside class="settings-paths-guide" aria-label="How paths work">
    <p class="settings-heading">How paths work</p>
    ${lines.join("")}
  </aside>`;
}

export function defaultClientSettingsDescriptor(
  showAdminIngest: boolean,
  canUpload: boolean,
  profile: DesktopProfile = "client-only",
): SettingsPanelDescriptor {
  const ingestHost = ingestHostLabel(profile);
  return {
    pathsGuideHtml: settingsPathsGuideMarkup(profile, showAdminIngest, canUpload),
    showOrchestratorUrl: true,
    showLibraryRoots: true,
    showPushUpload: canUpload,
    ingestLabels: {
      heading: `Server ingest paths (indexing on ${ingestHost})`,
      hint: `Folders on <strong>${ingestHost}</strong> that Lumogis watches and adds to household memory. This is separate from library roots above — ingest paths control <em>what gets indexed</em>, not where Open/reveal looks on your computer.`,
      restartBannerText: "Server ingest paths changed — restart to apply.",
      restartConfirmText:
        "Apply pending ingest path changes and restart the orchestrator stack?\n\nThis recreates containers (brief downtime).",
      saveSuccessHint: "Ingest paths saved. Restart when prompted if paths changed.",
    },
    searchCopy: {
      placeholder: "Search your household memory…",
      loginHint: "Sign in to search your household memory.",
      emptyHint: "Start typing to search your household memory…",
      rootsBanner: "Add library roots in Settings to open files locally.",
    },
  };
}

export function resolveSettingsPanel<TCtx>(
  ctx: TCtx,
  customize: ((ctx: TCtx, defaults: SettingsPanelDescriptor) => SettingsPanelDescriptor) | undefined,
  showAdminIngest: boolean,
  canUpload: boolean,
  profile: DesktopProfile,
): SettingsPanelDescriptor {
  const defaults = defaultClientSettingsDescriptor(showAdminIngest, canUpload, profile);
  return customize?.(ctx, defaults) ?? defaults;
}

export function themeToggleMarkup(themeMode: ThemeMode): string {
  const options: { id: ThemeMode; label: string; icon?: string }[] = [
    { id: "system", label: "System" },
    { id: "light", label: "Light", icon: "sun" },
    { id: "dark", label: "Dark", icon: "moon" },
  ];
  const buttons = options
    .map(
      (o) =>
        `<button type="button" class="segmented__btn theme-option" data-theme-value="${o.id}" aria-pressed="${themeMode === o.id}">${o.icon ? iconMarkup(o.icon, 13) : ""}${o.label}</button>`,
    )
    .join("");
  return `
    <div class="settings-field">
      <span class="settings-field__label">Theme</span>
      <div class="segmented" role="group" aria-label="Theme">${buttons}</div>
    </div>`;
}

export function ingestPathsEditorMarkup(
  labels: IngestSectionLabels,
  paths: string[],
  paperlessConfigured: boolean,
  restartRequired: boolean,
): string {
  const rows = paths
    .map(
      (p) =>
        `<div class="ingest-path-row">
          <input type="text" class="field ingest-path-input" value="${escapeHtml(p)}" placeholder="Host path (e.g. ./lumogis-data)" />
          <button type="button" class="btn btn--ghost btn--sm ingest-path-remove" title="Remove">×</button>
        </div>`,
    )
    .join("");
  const paperless = paperlessConfigured
    ? `<span class="badge ok">Paperless connected</span>`
    : `<span class="badge muted">Paperless not configured</span>`;
  const restartBanner = restartRequired
    ? `<div class="restart-banner" id="restart-banner">
        <span style="color:var(--accent-ink);flex-shrink:0">${iconMarkup("alert", 15)}</span>
        <p class="restart-banner__text">${labels.restartBannerText}</p>
        <button type="button" class="btn btn--primary btn--sm" id="btn-restart-stack">Restart</button>
      </div>`
    : "";
  return `
    <section class="settings-section">
      <h2 class="settings-heading">${labels.heading}</h2>
      <p class="hint">${labels.hint}</p>
      ${paperless}
      <div id="ingest-paths-list">${rows}</div>
      <div class="toolbar">
        <input type="text" class="field" id="new-ingest-path" placeholder="Add path…" style="min-height:34px" />
        <button type="button" class="btn btn--secondary btn--sm" id="btn-add-ingest-path">+ Add path</button>
      </div>
      <div class="toolbar">
        <button type="button" class="btn btn--secondary btn--sm" id="btn-save-ingest-paths">Save ingest paths</button>
      </div>
      ${restartBanner}
      <p class="hint" id="ingest-admin-hint"></p>
    </section>`;
}

export function uploadSectionMarkup(): string {
  return `
    <section class="settings-section">
      <h2 class="settings-heading">Push upload (one-off file to server)</h2>
      <p class="hint">Send a single file from this computer into your <strong>personal ingest queue</strong> on the household server. It will be indexed like a file dropped into an ingest folder — useful when you do not want to add a whole directory.</p>
      <input type="file" id="ingest-upload-input" class="field" style="min-height:auto;padding:var(--sp-2)" />
      <p class="hint" id="upload-hint"></p>
    </section>`;
}

export function composeSettingsBody(opts: ComposeSettingsBodyOptions): string {
  const { descriptor } = opts;
  const guide = descriptor.pathsGuideHtml;
  const orchestratorField = descriptor.showOrchestratorUrl
    ? `<div class="settings-field">
        <label class="settings-field__label" for="set-base">Orchestrator base URL</label>
        <input id="set-base" class="field" type="url" />
      </div>`
    : "";
  const libraryRootsField = descriptor.showLibraryRoots
    ? `<div class="settings-field">
        <label class="settings-field__label" for="set-roots">Library roots — on this computer (one folder per line)</label>
        <textarea id="set-roots" class="field" rows="3" spellcheck="false"></textarea>
        ${
          opts.showPickLibraryFolder
            ? `<button type="button" class="btn btn--secondary btn--sm" id="btn-pick-library-root" style="align-self:flex-start;margin-top:2px">${iconMarkup("folder", 14)} Choose folder…</button>`
            : ""
        }
        <p class="hint">When you pick a search result, Lumogis opens or reveals the file under these folders. They do <strong>not</strong> tell the server what to index.</p>
      </div>`
    : "";
  const ingestSection = opts.showAdminIngest
    ? ingestPathsEditorMarkup(
        descriptor.ingestLabels,
        opts.ingestPaths,
        opts.paperlessConfigured,
        opts.restartRequired,
      )
    : "";
  const uploadSection = descriptor.showPushUpload ? uploadSectionMarkup() : "";

  return `
    <div class="settings-panel" id="settings">
      ${guide}
      ${themeToggleMarkup(opts.themeMode)}
      ${orchestratorField}
      <div class="settings-field">
        <label class="settings-field__label" for="set-hotkey">Global hotkey</label>
        <input id="set-hotkey" class="field" type="text" spellcheck="false" />
        <p class="hint">Default <code>CommandOrControl+Shift+L</code>. Invalid values are not saved.</p>
      </div>
      ${libraryRootsField}
      ${ingestSection}
      ${uploadSection}
      <p class="hint" id="session-hint"></p>
      <div class="settings-footer">
        <button type="button" class="btn btn--primary btn--sm" id="btn-save">Save settings</button>
        ${
          opts.needsLogin
            ? `<button type="button" class="btn btn--ghost btn--sm" id="btn-open-login">Sign in…</button>`
            : `<button type="button" class="btn btn--ghost btn--sm" id="btn-logout" ${opts.authMode !== "on" || !opts.sessionPresent ? "disabled" : ""}>Sign out</button>`
        }
        <span class="settings-footer__status">${opts.overlayStatusPillHtml}</span>
      </div>
      <p class="hint" id="keychain-hint"></p>
    </div>`;
}

export type OverlaySettingsSaveDom = {
  orchestratorBaseUrl?: string;
  hotkey: string;
  libraryRootsRaw?: string;
};

export function overlaySettingsSavePayload(
  descriptor: SettingsPanelDescriptor,
  settings: { orchestratorBaseUrl: string; libraryRoots: string[] },
  dom: OverlaySettingsSaveDom,
  theme: ThemeMode,
): OverlaySettingsSaveInput {
  const orchestratorBaseUrl = descriptor.showOrchestratorUrl
    ? (dom.orchestratorBaseUrl ?? "").trim()
    : settings.orchestratorBaseUrl;
  const rootsRaw = descriptor.showLibraryRoots ? (dom.libraryRootsRaw ?? "") : settings.libraryRoots.join("\n");
  const libraryRoots = descriptor.showLibraryRoots
    ? rootsRaw
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
    : [...settings.libraryRoots];
  return {
    orchestratorBaseUrl,
    hotkey: dom.hotkey.trim(),
    libraryRoots,
    theme,
  };
}

/** Golden-test fixture inputs (Pass 0 baseline capture). */
export const GOLDEN_ADMIN_COMPOSE_OPTS: ComposeSettingsBodyOptions = {
  descriptor: defaultClientSettingsDescriptor(true, true, "client-only"),
  themeMode: "system",
  showAdminIngest: true,
  showPickLibraryFolder: false,
  needsLogin: false,
  authMode: "on",
  sessionPresent: true,
  ingestPaths: ["/home/operator/lumogis-data"],
  paperlessConfigured: false,
  restartRequired: false,
  overlayStatusPillHtml: "",
};

export const GOLDEN_MEMBER_COMPOSE_OPTS: ComposeSettingsBodyOptions = {
  descriptor: defaultClientSettingsDescriptor(false, true, "client-only"),
  themeMode: "system",
  showAdminIngest: false,
  showPickLibraryFolder: false,
  needsLogin: false,
  authMode: "on",
  sessionPresent: true,
  ingestPaths: [],
  paperlessConfigured: false,
  restartRequired: false,
  overlayStatusPillHtml: "",
};
