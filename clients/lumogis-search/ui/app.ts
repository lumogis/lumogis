// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  fetchMemorySearch,
  type MemorySearchHit,
  type MemorySearchResponse,
} from "./searchClient";
import {
  applyTheme,
  normalizeTheme,
  watchSystemTheme,
  type ThemeMode,
} from "./theme";
import { formatHotkeyForDisplay } from "./hotkeyDisplay";
import { createHitRow } from "./hitRow";
import { iconMarkup, logoDragHandleMarkup, statusPillMarkup } from "./primitives";
import { focusSearchInput, wireLogoDrag } from "./overlayWindow";
import {
  canManageIngestPaths,
  canUploadIngest,
  isSearchDisabled,
  needsOnboarding,
  onboardingMarkup,
  type AuthMode,
} from "./overlayUi";
import {
  activateSummonHint,
  dismissSummonHint,
  isRecoveryHintActive,
  isSummonHintActive,
  offerRecoveryHintIfNeeded,
  offerSummonHintIfPending,
  upsertRecoveryHintElement,
  upsertSummonHintElement,
} from "./summonHint";
import {
  composeSettingsBody,
  overlaySettingsSavePayload,
  resolveSettingsPanel,
  type DesktopProfile,
  type SettingsPanelDescriptor,
} from "./settingsPanel";

export type {
  DesktopProfile,
  IngestSectionLabels,
  SearchCopy,
  SettingsPanelDescriptor,
} from "./settingsPanel";

/** Overlay settings DTO (the OverlayConfig-shaped type used by the Rust side). */
export type OverlaySettings = {
  schemaVersion: number;
  orchestratorBaseUrl: string;
  hotkey: string;
  libraryRoots: string[];
  theme: string;
  onboardingComplete?: boolean;
  keychainError?: string | null;
  authMode?: string;
  sessionPresent?: boolean;
  sessionRole?: string | null;
};

/**
 * Stable read/controlled-write surface the factory passes to every hook.
 * Hooks never reach into private factory state directly. Listeners registered
 * via `listen` persist for the window lifetime (matching the overlay's behaviour;
 * there is no teardown/auto-unsubscribe).
 */
export interface OverlayAppContext {
  /** Desktop profile resolved at boot, before the first render(). */
  readonly profile: DesktopProfile;
  /** Live overlay settings (read-mostly). */
  readonly settings: OverlaySettings;
  /** Root container the shared shell renders into. */
  readonly root: HTMLElement;
  /** Re-run the shared render() with current state (idempotent). */
  render(): void;
  /** Re-read settings from Rust (get_overlay_settings) then render(). */
  refreshSettings(): Promise<void>;
  /** Persist new library roots (save_overlay_settings) then refresh + render(). */
  setLibraryRoots(roots: string[]): Promise<void>;
  /** Tauri invoke passthrough (typed). */
  invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T>;
  /** Tauri event listen passthrough. */
  listen(event: string, cb: (payload: unknown) => void): Promise<void>;
}

/**
 * Optional hook injection points (all optional). With no hooks the behaviour is
 * byte-identical to the default overlay. An embedding client may override
 * profile resolution, onboarding, search gating, banners, ingest visibility,
 * and post-boot setup.
 */
export interface OverlayAppHooks {
  /** Resolve the desktop profile (default: invoke `get_desktop_profile`); resolves before the first render(). */
  resolveProfile?(): Promise<DesktopProfile>;
  /** Override onboarding gate (default: shared `needsOnboarding`). */
  shouldShowOnboarding?(ctx: OverlayAppContext): boolean;
  /** Render a custom onboarding wizard; return true if it rendered (default absent → shared wizard). */
  renderOnboarding?(ctx: OverlayAppContext): boolean;
  /** Override whether the search box is disabled (default: shared isSearchDisabled). */
  computeSearchDisabled?(ctx: OverlayAppContext): boolean;
  /** A banner string to show atop the main shell, or null/absent for none. */
  startingBanner?(ctx: OverlayAppContext): string | null;
  /** Hide the admin ingest panel (default absent → shown when permitted). */
  hideAdminIngest?(ctx: OverlayAppContext): boolean;
  /** Post-boot hook (e.g. register host-specific listeners and call ctx.render()). */
  onBoot?(ctx: OverlayAppContext): Promise<void>;
  /** After settings load, before first render (e.g. restore bundled wizard state). */
  prepareBoot?(ctx: OverlayAppContext): Promise<void>;
  /** Native folder picker for library roots (Hub bundled); absent → type paths manually. */
  pickLibraryFolder?(ctx: OverlayAppContext): Promise<string | null>;
  /** After settings save (e.g. Hub: sync library root to bundled Core). */
  afterSaveSettings?(ctx: OverlayAppContext, libraryRoots: string[]): Promise<void>;
  /** HTML prepended to the main overlay shell (e.g. Hub running indicator). */
  overlayChrome?(ctx: OverlayAppContext): string | null;
  /** Persona-specific settings panel visibility and copy (default: client-only A/B layout). */
  customizeSettingsPanel?(
    ctx: OverlayAppContext,
    defaults: SettingsPanelDescriptor,
  ): SettingsPanelDescriptor;
}

/**
 * Shared overlay application factory.
 *
 * The former `main.ts` body lives here. Optional `hooks` let an embedding
 * client customise behaviour without forking. With no hooks, rendered markup
 * matches the standalone Search entry.
 */
export function createOverlayApp(hooks: OverlayAppHooks = {}) {
type AuthProbe = {
  mode: string;
  sessionPresent: boolean;
  role?: string | null;
};

const root = document.querySelector<HTMLDivElement>("#root")!;

let settings: OverlaySettings = {
  schemaVersion: 2,
  orchestratorBaseUrl: "http://127.0.0.1:8000",
  hotkey: "CommandOrControl+Shift+L",
  libraryRoots: [],
  theme: "system",
  onboardingComplete: false,
  keychainError: null,
};

let wizardBaseUrl = "";
let wizardHealthStatus = "idle";
let wizardHealthMessage = "";
let wizardLoginError = "";

let authMode: AuthMode = "unknown";
let sessionPresent = false;
let sessionRole: string | null = null;

type AdminSettings = {
  ingestPaths: string[];
  pendingIngestPaths: string[] | null;
  restartRequired: boolean;
  paperlessConfigured: boolean;
};

let adminSettings: AdminSettings | null = null;

let lastController: AbortController | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let unwatchSystemTheme: (() => void) | null = null;
let settingsViewOpen = false;
let selectedHitIndex = 0;
let keyboardNavBound = false;

let profile: DesktopProfile = "client-only";
let currentSettingsDescriptor: SettingsPanelDescriptor;

// Stable hook context over this factory's state. Getters keep `settings`/`profile`
// live across reassignment. With no hooks it is constructed but never used.
const ctx: OverlayAppContext = {
  get profile() {
    return profile;
  },
  get settings() {
    return settings;
  },
  get root() {
    return root;
  },
  render() {
    render();
  },
  async refreshSettings() {
    await refreshSettingsFromRust();
    render();
  },
  async setLibraryRoots(roots: string[]) {
    await invoke("save_overlay_settings", {
      orchestratorBaseUrl: settings.orchestratorBaseUrl,
      hotkey: settings.hotkey,
      libraryRoots: roots,
      theme: currentTheme(),
    });
    await refreshSettingsFromRust();
    render();
  },
  invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
    return invoke<T>(cmd, args);
  },
  async listen(event: string, cb: (payload: unknown) => void): Promise<void> {
    await listen(event, (ev) => cb(ev.payload));
  },
};

async function defaultResolveProfile(): Promise<DesktopProfile> {
  try {
    const p = await invoke<string>("get_desktop_profile");
    if (p === "bundled") return "bundled";
    if (p === "server") return "server";
    return "client-only";
  } catch {
    return "client-only";
  }
}

function currentTheme(): ThemeMode {
  return normalizeTheme(settings.theme);
}

function setThemeMode(mode: ThemeMode): void {
  settings.theme = mode;
  applyTheme(mode);
}

function updateQueryEmptyState(query: string): void {
  const empty = query.trim().length === 0;
  root.classList.toggle("query-empty", empty);
  const hint = root.querySelector<HTMLParagraphElement>("#search-hint");
  if (hint) {
    hint.hidden = !empty;
  }
}

function needsLogin(): boolean {
  return authMode === "on" && !sessionPresent;
}

function editorIngestPaths(): string[] {
  if (!adminSettings) return [];
  if (adminSettings.pendingIngestPaths?.length) {
    return [...adminSettings.pendingIngestPaths];
  }
  return [...adminSettings.ingestPaths];
}

function mapSearchError(err: unknown): string {
  const s = String(err);
  if (s.includes("session_expired")) {
    return "Session expired — sign in again.";
  }
  if (s.includes("auth_csrf_misconfig")) {
    return "Auth misconfiguration (CSRF) — check LUMOGIS_PUBLIC_ORIGIN on the server.";
  }
  if (s.includes("http_401") || s.includes("missing_or_invalid")) {
    return "Not signed in — open Settings and sign in.";
  }
  if (s.includes("http_403") || s.includes("forbidden")) {
    return "Forbidden for this user — check orchestrator roles.";
  }
  if (s.includes("http_429") || s.includes("rate_limited")) {
    return "Too many requests — wait and retry.";
  }
  if (s.includes("http_5xx") || s.includes("server_error")) {
    return "Orchestrator error — check logs.";
  }
  if (s.includes("timeout") || s.includes("connection_failed")) {
    return "Stack appears down — ensure `docker compose up -d` and the base URL is correct.";
  }
  if (s.includes("non_json") || s.includes("invalid_json")) {
    return "Unexpected response (not JSON) — check reverse proxy / orchestrator URL.";
  }
  if (s.includes("cancelled")) {
    return "";
  }
  return "Search failed — see README troubleshooting.";
}

function overlayStatusPillMarkup(): string {
  if (profile === "server") {
    const starting = hooks.startingBanner?.(ctx);
    if (!starting) {
      return statusPillMarkup("Server running");
    }
    return statusPillMarkup("Starting…", "warn");
  }
  if (profile === "bundled") {
    const starting = hooks.startingBanner?.(ctx);
    if (!starting) {
      return statusPillMarkup("Hub running");
    }
    return statusPillMarkup("Starting…", "warn");
  }
  if (authMode === "unreachable") {
    return statusPillMarkup("Unreachable", "warn");
  }
  return "";
}

function overlayFooterMarkup(hotkey: string): string {
  const hk = formatHotkeyForDisplay(hotkey);
  const isMac = hk.length <= 4 && !hk.includes("+");
  const hints: [string, string][] = [
    ["↑↓", "navigate"],
    ["↵", "open"],
    [isMac ? "⌥↵" : "Shift+↵", "reveal"],
    ["esc", settingsViewOpen ? "close" : "hide"],
  ];
  const hintHtml = hints
    .map(
      ([k, label]) =>
        `<span class="overlay-kbd-hint"><kbd>${k}</kbd>${label}</span>`,
    )
    .join("");
  return `<div class="overlay-footer">
    ${hintHtml}
    <span class="overlay-footer__hotkey">${escapeHtml(hk)}</span>
  </div>`;
}

function shouldShowOnboarding(): boolean {
  return hooks.shouldShowOnboarding?.(ctx) ?? needsOnboarding(settings);
}

function render() {
  if (shouldShowOnboarding()) {
    if (hooks.renderOnboarding?.(ctx)) {
      return;
    }
    wizardBaseUrl = wizardBaseUrl || settings.orchestratorBaseUrl;
    root.innerHTML = onboardingMarkup({
      wizardBaseUrl,
      healthStatus: wizardHealthStatus,
      healthMessage: wizardHealthMessage,
      authMode,
      sessionPresent,
      loginError: wizardLoginError,
    });
    wireOnboarding();
    return;
  }

  root.classList.remove("hub-setup-mode");

  const rootsEmpty = settings.libraryRoots.length === 0;
  const searchDisabled = hooks.computeSearchDisabled
    ? hooks.computeSearchDisabled(ctx)
    : isSearchDisabled(needsLogin(), authMode);
  const startingBanner = hooks.startingBanner?.(ctx) ?? null;
  const overlayChrome = hooks.overlayChrome?.(ctx) ?? "";
  const showAdminIngest =
    canManageIngestPaths(authMode, sessionRole) && !hooks.hideAdminIngest?.(ctx);
  const canUpload = canUploadIngest(authMode, sessionPresent);
  currentSettingsDescriptor = resolveSettingsPanel(
    ctx,
    hooks.customizeSettingsPanel
      ? (c, defaults) => hooks.customizeSettingsPanel!(c, defaults)
      : undefined,
    showAdminIngest,
    canUpload,
    profile,
  );
  const searchCopy = currentSettingsDescriptor.searchCopy;
  const prevQuery = root.querySelector<HTMLInputElement>("#q")?.value ?? "";
  const hasResults = Boolean(prevQuery.trim());

  root.classList.toggle("view-settings", settingsViewOpen);
  root.classList.toggle("has-results", hasResults);

  const searchBody = settingsViewOpen
    ? ""
    : `
    <div class="overlay-messages">
      ${overlayChrome}
      ${startingBanner ? `<div class="banner warn" id="starting-banner">${escapeHtml(startingBanner)}</div>` : ""}
      ${authMode === "unreachable" && !startingBanner ? `<div class="banner err" id="auth-unreachable">Could not reach Lumogis — check the base URL and that the stack is running.</div>` : ""}
      ${needsLogin() ? `<div class="login-panel" id="login-panel">
        <p class="hint">${searchCopy.loginHint}</p>
        <label>Email <input id="login-email" class="field" type="email" autocomplete="username" /></label>
        <label>Password <input id="login-password" class="field" type="password" minlength="12" autocomplete="current-password" /></label>
        <div class="toolbar">
          <button type="button" class="btn btn--primary btn--sm" id="btn-login">Sign in</button>
        </div>
        <p class="hint err-text" id="login-error"></p>
      </div>` : ""}
      ${rootsEmpty && currentSettingsDescriptor.showLibraryRoots && searchCopy.rootsBanner ? `<div class="banner warn" id="roots-banner">${searchCopy.rootsBanner}</div>` : ""}
      <p id="search-hint" class="hint search-empty-hint">${searchCopy.emptyHint}</p>
      <div id="degraded" class="banner warn" style="display:none"></div>
      <div id="error" class="banner err" style="display:none"></div>
    </div>
    <div class="results" id="results"></div>
    ${overlayFooterMarkup(settings.hotkey)}`;

  const settingsBody = composeSettingsBody({
    descriptor: currentSettingsDescriptor,
    themeMode: currentTheme(),
    showAdminIngest,
    showPickLibraryFolder: Boolean(hooks.pickLibraryFolder),
    needsLogin: needsLogin(),
    authMode,
    sessionPresent,
    ingestPaths: editorIngestPaths(),
    paperlessConfigured: adminSettings?.paperlessConfigured ?? false,
    restartRequired: adminSettings?.restartRequired ?? false,
    overlayStatusPillHtml: overlayStatusPillMarkup(),
  });

  root.innerHTML = `
    <div class="overlay-card">
      <div class="overlay-searchbar">
        ${logoDragHandleMarkup(22)}
        <input type="search" id="q" placeholder="${searchCopy.placeholder}" autocomplete="off" ${searchDisabled ? "disabled" : ""}${searchDisabled ? "" : " autofocus"} />
        ${overlayStatusPillMarkup()}
        <button type="button" id="btn-settings" class="overlay-gear${settingsViewOpen ? " overlay-gear--active" : ""}" aria-label="Search settings" title="Settings">${iconMarkup("settings", 16)}</button>
      </div>
      <div class="overlay-body">
        ${settingsViewOpen ? settingsBody : searchBody}
      </div>
    </div>
  `;

  const q = root.querySelector<HTMLInputElement>("#q")!;
  q.value = prevQuery;

  applyTheme(currentTheme());
  updateQueryEmptyState(q.value);

  q.addEventListener("input", () => {
    selectedHitIndex = 0;
    updateQueryEmptyState(q.value);
    root.classList.toggle("has-results", q.value.trim().length > 0);
    if (debounceTimer) clearTimeout(debounceTimer);
    const results = root.querySelector<HTMLDivElement>("#results");
    const degraded = root.querySelector<HTMLDivElement>("#degraded");
    const errBox = root.querySelector<HTMLDivElement>("#error");
    if (results && degraded && errBox) {
      debounceTimer = setTimeout(() => void runSearch(q.value, results, degraded, errBox), 250);
    }
  });

  root.querySelector("#btn-settings")!.addEventListener("click", () => {
    void toggleSettingsPanel();
  });

  const panel = root.querySelector<HTMLDivElement>("#settings");
  panel?.querySelector("#btn-pick-library-root")?.addEventListener("click", () => {
    void pickLibraryRootIntoSettings();
  });
  panel?.querySelector("#btn-save")?.addEventListener("click", () => {
    if (panel) void saveAllSettings(panel);
  });
  const logoutBtn = panel?.querySelector<HTMLButtonElement>("#btn-logout");
  logoutBtn?.addEventListener("click", () => void signOut());
  const openLoginBtn = panel?.querySelector<HTMLButtonElement>("#btn-open-login");
  openLoginBtn?.addEventListener("click", () => {
    settingsViewOpen = false;
    render();
    root.querySelector<HTMLInputElement>("#login-email")?.focus();
  });

  const loginBtn = root.querySelector<HTMLButtonElement>("#btn-login");
  loginBtn?.addEventListener("click", () => void submitLogin());

  if (panel) {
    wireIngestPathsEditor(panel);
    if (currentSettingsDescriptor.showPushUpload) {
      wireUploadInput(panel);
    }
    for (const btn of panel.querySelectorAll<HTMLButtonElement>(".theme-option")) {
      btn.addEventListener("click", () => {
        const value = normalizeTheme(btn.dataset.themeValue ?? "system");
        setThemeMode(value);
        for (const b of panel.querySelectorAll<HTMLButtonElement>(".theme-option")) {
          b.setAttribute("aria-pressed", String(b.dataset.themeValue === value));
        }
      });
    }
    if (settingsViewOpen) {
      fillSettingsForm();
    }
  }

  if (!settingsViewOpen) {
    const results = root.querySelector<HTMLDivElement>("#results");
    const degraded = root.querySelector<HTMLDivElement>("#degraded");
    const errBox = root.querySelector<HTMLDivElement>("#error");
    if (results && degraded && errBox) {
      void runSearch(q.value, results, degraded, errBox);
    }
  }

  wireKeyboardNavigation();
  wireLogoDrag(root);
  focusSearchInput(root, settingsViewOpen);

  if (isSummonHintActive()) {
    upsertSummonHintElement(root, settings.hotkey);
  }
  if (isRecoveryHintActive()) {
    upsertRecoveryHintElement(root, ctx);
  }
}

async function toggleSettingsPanel() {
  if (!settingsViewOpen) {
    await loadAdminSettingsIfNeeded();
    settingsViewOpen = true;
  } else {
    settingsViewOpen = false;
  }
  render();
}

function closeSettingsPanel() {
  if (!settingsViewOpen) {
    return;
  }
  settingsViewOpen = false;
  render();
}

function wireKeyboardNavigation() {
  if (keyboardNavBound) {
    return;
  }
  keyboardNavBound = true;
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && settingsViewOpen) {
      e.preventDefault();
      closeSettingsPanel();
      return;
    }
    if (settingsViewOpen || shouldShowOnboarding()) {
      return;
    }
    const results = root.querySelector<HTMLDivElement>("#results");
    if (!results) {
      return;
    }
    const rows = Array.from(results.querySelectorAll<HTMLButtonElement>(".hit-row:not(.hit-row--disabled)"));
    if (!rows.length) {
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selectedHitIndex = Math.min(selectedHitIndex + 1, rows.length - 1);
      updateHitSelection(rows);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      selectedHitIndex = Math.max(selectedHitIndex - 1, 0);
      updateHitSelection(rows);
    } else if (e.key === "Enter" && document.activeElement?.id === "q") {
      const row = rows[selectedHitIndex];
      if (!row) {
        return;
      }
      e.preventDefault();
      if (e.altKey || e.shiftKey) {
        row.dispatchEvent(new MouseEvent("click", { shiftKey: true, bubbles: true }));
      } else {
        row.click();
      }
    }
  });
}

function updateHitSelection(rows: HTMLButtonElement[]) {
  rows.forEach((row, i) => {
    row.classList.toggle("hit-row--selected", i === selectedHitIndex);
  });
  rows[selectedHitIndex]?.scrollIntoView({ block: "nearest" });
}

async function pickLibraryRootIntoSettings() {
  if (!hooks.pickLibraryFolder) {
    return;
  }
  const picked = await hooks.pickLibraryFolder(ctx);
  if (!picked) {
    return;
  }
  const area = root.querySelector<HTMLTextAreaElement>("#set-roots");
  if (!area) {
    return;
  }
  const lines = area.value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  if (!lines.includes(picked)) {
    lines.unshift(picked);
  }
  area.value = lines.join("\n");
}

async function loadAdminSettingsIfNeeded() {
  if (!canManageIngestPaths(authMode, sessionRole)) {
    adminSettings = null;
    return;
  }
  try {
    adminSettings = await invoke<AdminSettings>("fetch_admin_settings");
  } catch (e) {
    adminSettings = null;
    console.warn("fetch_admin_settings failed", e);
  }
}

function collectIngestPathsFromEditor(): string[] {
  return Array.from(root.querySelectorAll<HTMLInputElement>(".ingest-path-input"))
    .map((el) => el.value.trim())
    .filter(Boolean);
}

function wireIngestPathsEditor(panel: HTMLElement) {
  const addBtn = panel.querySelector<HTMLButtonElement>("#btn-add-ingest-path");
  if (!addBtn) return;
  addBtn.addEventListener("click", () => {
    const input = panel.querySelector<HTMLInputElement>("#new-ingest-path");
    const list = panel.querySelector<HTMLDivElement>("#ingest-paths-list");
    if (!input || !list || !input.value.trim()) return;
    const row = document.createElement("div");
    row.className = "ingest-path-row";
    row.innerHTML = `
      <input type="text" class="field ingest-path-input" value="${escapeHtml(input.value.trim())}" placeholder="Host path" />
      <button type="button" class="btn btn--ghost btn--sm ingest-path-remove" title="Remove">×</button>`;
    list.appendChild(row);
    input.value = "";
  });
  panel.querySelector("#btn-save-ingest-paths")?.addEventListener("click", () => {
    void saveIngestPaths(panel);
  });
  panel.querySelector("#btn-restart-stack")?.addEventListener("click", () => {
    void applyRestart(panel);
  });
  panel.addEventListener("click", (ev) => {
    const t = ev.target as HTMLElement;
    if (t.classList.contains("ingest-path-remove")) {
      t.closest(".ingest-path-row")?.remove();
    }
  });
}

function wireUploadInput(panel: HTMLElement) {
  const input = panel.querySelector<HTMLInputElement>("#ingest-upload-input");
  if (!input) return;
  input.addEventListener("change", () => {
    void handleUploadSelected(panel, input);
  });
}

async function saveIngestPaths(panel: HTMLElement) {
  const hint = panel.querySelector<HTMLParagraphElement>("#ingest-admin-hint");
  const paths = collectIngestPathsFromEditor();
  if (!paths.length) {
    if (hint) hint.textContent = "Add at least one ingest path.";
    return;
  }
  try {
    adminSettings = await invoke<AdminSettings>("save_admin_ingest_paths", { ingestPaths: paths });
    if (hint) hint.textContent = currentSettingsDescriptor.ingestLabels.saveSuccessHint;
    if (adminSettings.restartRequired) {
      settingsViewOpen = true;
      render();
      fillSettingsForm();
    }
  } catch (e) {
    if (hint) hint.textContent = String(e);
  }
}

async function applyRestart(panel: HTMLElement) {
  const hint = panel.querySelector<HTMLParagraphElement>("#ingest-admin-hint");
  const ok = confirm(currentSettingsDescriptor.ingestLabels.restartConfirmText);
  if (!ok) return;
  try {
    await invoke("restart_orchestrator_stack");
    if (hint) {
      hint.textContent =
        "Restart requested — the stack may be unreachable for a minute. Re-open Settings to refresh status.";
    }
    await new Promise((r) => setTimeout(r, 2000));
    await refreshAuthFromRust();
    await loadAdminSettingsIfNeeded();
    fillSettingsForm();
  } catch (e) {
    const msg = String(e);
    if (hint) {
      hint.textContent = msg.includes("connection")
        ? "Restart likely succeeded but the connection dropped — wait and refresh."
        : msg;
    }
  }
}

async function handleUploadSelected(panel: HTMLElement, input: HTMLInputElement) {
  const hint = panel.querySelector<HTMLParagraphElement>("#upload-hint");
  const file = input.files?.[0];
  if (!file) return;
  if (hint) hint.textContent = "Uploading…";
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    const out = await invoke<{ status: string; fileId: string }>("upload_ingest_file", {
      fileName: file.name,
      bytes: Array.from(buf),
    });
    if (hint) {
      hint.textContent = `Queued (${out.status}) — file id ${out.fileId}`;
    }
  } catch (e) {
    const msg = String(e);
    if (hint) {
      if (msg.includes("unsupported_extension")) {
        hint.textContent = "Unsupported file type for ingest.";
      } else if (msg.includes("file_too_large")) {
        hint.textContent = "File exceeds server size limit.";
      } else if (msg.includes("session_expired")) {
        hint.textContent = "Session expired — sign in again.";
      } else {
        hint.textContent = msg;
      }
    }
  } finally {
    input.value = "";
  }
}

function fillSettingsForm() {
  const baseEl = root.querySelector<HTMLInputElement>("#set-base");
  if (baseEl) baseEl.value = settings.orchestratorBaseUrl;
  const hotkeyEl = root.querySelector<HTMLInputElement>("#set-hotkey");
  if (hotkeyEl) hotkeyEl.value = settings.hotkey;
  const rootsEl = root.querySelector<HTMLTextAreaElement>("#set-roots");
  if (rootsEl) rootsEl.value = settings.libraryRoots.join("\n");
  const sessionHint = root.querySelector("#session-hint")!;
  const keyHint = root.querySelector("#keychain-hint")!;
  if (authMode === "off") {
    sessionHint.textContent = "Auth is off on the server — search works without signing in.";
  } else if (sessionPresent) {
    sessionHint.textContent = `Signed in${sessionRole ? ` (${sessionRole})` : ""}. Session stored in OS keychain.`;
  } else if (authMode === "on") {
    sessionHint.textContent = "Not signed in — use the sign-in form on the main screen.";
  } else {
    sessionHint.textContent = "";
  }
  keyHint.textContent = settings.keychainError
    ? `Keychain: ${settings.keychainError} (see README — gnome-keyring / kwallet).`
    : "";
  const mode = currentTheme();
  for (const b of root.querySelectorAll<HTMLButtonElement>(".theme-option")) {
    b.setAttribute("aria-pressed", String(b.dataset.themeValue === mode));
  }
}

async function refreshSettingsFromRust() {
  const loaded = await invoke<OverlaySettings>("get_overlay_settings");
  settings = {
    ...loaded,
    theme: normalizeTheme(loaded.theme),
  };
  applyTheme(currentTheme());
}

async function refreshAuthFromRust(orchestratorBaseUrl?: string | null) {
  try {
    const probe = await invoke<AuthProbe>("probe_auth_state", {
      orchestratorBaseUrl: orchestratorBaseUrl ?? null,
    });
    authMode = (probe.mode as AuthMode) || "unknown";
    sessionPresent = Boolean(probe.sessionPresent);
    sessionRole = probe.role ?? null;
  } catch {
    // Core may still be starting (bundled Hub) or the stack may be down — not fatal.
    authMode = "unreachable";
    sessionPresent = false;
    sessionRole = null;
  }
}

async function saveAllSettings(panel: HTMLElement) {
  const payload = overlaySettingsSavePayload(
    currentSettingsDescriptor,
    settings,
    {
      orchestratorBaseUrl: root.querySelector<HTMLInputElement>("#set-base")?.value,
      hotkey: root.querySelector<HTMLInputElement>("#set-hotkey")?.value ?? "",
      libraryRootsRaw: root.querySelector<HTMLTextAreaElement>("#set-roots")?.value,
    },
    currentTheme(),
  );
  try {
    await invoke("save_overlay_settings", payload);
    await hooks.afterSaveSettings?.(ctx, payload.libraryRoots);
    await refreshSettingsFromRust();
    await refreshAuthFromRust();
    settingsViewOpen = false;
    render();
  } catch (e) {
    alert(String(e));
  }
}

async function submitLogin() {
  const email = (root.querySelector("#login-email") as HTMLInputElement | null)?.value.trim();
  const password = (root.querySelector("#login-password") as HTMLInputElement | null)?.value ?? "";
  const errEl = root.querySelector<HTMLParagraphElement>("#login-error");
  if (!email || password.length < 12) {
    if (errEl) errEl.textContent = "Email and password (min 12 characters) are required.";
    return;
  }
  try {
    const session = await invoke<{ role: string; present: boolean }>("auth_login", {
      email,
      password,
      orchestratorBaseUrl: null,
    });
    sessionPresent = session.present;
    sessionRole = session.role;
    authMode = "on";
    if (errEl) errEl.textContent = "";
    adminSettings = null;
    render();
  } catch (e) {
    const msg = String(e);
    if (errEl) {
      if (msg.includes("invalid_credentials")) {
        errEl.textContent = "Invalid email or password.";
      } else if (msg.includes("auth_disabled")) {
        errEl.textContent = "Server auth is disabled — no sign-in required.";
      } else if (msg.includes("rate_limited")) {
        errEl.textContent = "Too many attempts — wait a minute and retry.";
      } else {
        errEl.textContent = msg;
      }
    }
  }
}

async function signOut() {
  await invoke("clear_session");
  sessionPresent = false;
  sessionRole = null;
  adminSettings = null;
  await refreshAuthFromRust();
  render();
}

async function runSearch(
  q: string,
  results: HTMLDivElement,
  degraded: HTMLDivElement,
  errBox: HTMLDivElement,
) {
  if (shouldShowOnboarding()) {
    return;
  }
  degraded.style.display = "none";
  errBox.style.display = "none";
  results.innerHTML = "";
  const query = q.trim();
  if (query.length === 0) {
    return;
  }
  if (authMode === "unreachable") {
    await refreshAuthFromRust();
    if (authMode === "unreachable") {
      errBox.style.display = "block";
      errBox.textContent = mapSearchError("connection_failed");
      return;
    }
    if (needsLogin()) {
      return;
    }
  }
  if (needsLogin()) {
    return;
  }
  lastController?.abort();
  const ac = new AbortController();
  lastController = ac;
  try {
    const data: MemorySearchResponse = await fetchMemorySearch(
      settings.orchestratorBaseUrl,
      null,
      query,
      ac.signal,
    );
    if (data.degraded) {
      degraded.style.display = "block";
      degraded.textContent = `Degraded: ${data.reason ?? "unknown"}`;
    }
    if (!data.hits.length) {
      results.innerHTML = `<p class="hint">No hits.</p>`;
      return;
    }
    const hits = data.hits.slice(0, 5);
    if (selectedHitIndex >= hits.length) {
      selectedHitIndex = 0;
    }
    hits.forEach((h, i) => {
      results.appendChild(createHitRow(h, settings.libraryRoots, invoke, i === selectedHitIndex));
    });
  } catch (e) {
    if (ac.signal.aborted) return;
    const raw = String(e);
    if (raw.includes("session_expired")) {
      sessionPresent = false;
      sessionRole = null;
      render();
    }
    const msg = mapSearchError(e);
    if (!msg) return;
    errBox.style.display = "block";
    errBox.textContent = msg;
  }
}

function escapeHtml(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function wireOnboarding() {
  const baseInput = root.querySelector<HTMLInputElement>("#onboard-base");
  baseInput?.addEventListener("input", () => {
    wizardBaseUrl = baseInput.value.trim();
    wizardHealthStatus = "idle";
    wizardHealthMessage = "";
    wizardLoginError = "";
  });

  root.querySelector("#btn-test-connection")?.addEventListener("click", () => {
    void testOnboardingConnection();
  });

  root.querySelector("#btn-onboard-login")?.addEventListener("click", () => {
    void submitOnboardingLogin();
  });

  root.querySelector("#btn-onboard-continue")?.addEventListener("click", () => {
    void finishOnboarding();
  });
}

async function testOnboardingConnection() {
  const baseInput = root.querySelector<HTMLInputElement>("#onboard-base");
  wizardBaseUrl = baseInput?.value.trim() ?? "";
  if (!wizardBaseUrl) {
    wizardHealthStatus = "unreachable";
    wizardHealthMessage = "Enter a server URL.";
    render();
    return;
  }
  try {
    const health = await invoke<{ status: string; message?: string | null }>("probe_server_health", {
      orchestratorBaseUrl: wizardBaseUrl,
    });
    wizardHealthStatus = health.status;
    wizardHealthMessage =
      health.message ??
      (health.status === "unreachable"
        ? "Cannot reach server — check URL and network."
        : health.status === "degraded"
          ? "Unexpected server response — retry."
          : "");
    if (health.status === "ok") {
      await refreshAuthFromRust(wizardBaseUrl);
    }
  } catch (e) {
    wizardHealthStatus = "unreachable";
    wizardHealthMessage = String(e);
  }
  render();
}

async function submitOnboardingLogin() {
  const email = (root.querySelector("#onboard-email") as HTMLInputElement | null)?.value.trim();
  const password = (root.querySelector("#onboard-password") as HTMLInputElement | null)?.value ?? "";
  if (!email || password.length < 12) {
    wizardLoginError = "Email and password (min 12 characters) are required.";
    render();
    return;
  }
  try {
    const session = await invoke<{ role: string; present: boolean }>("auth_login", {
      email,
      password,
      orchestratorBaseUrl: wizardBaseUrl,
    });
    sessionPresent = session.present;
    sessionRole = session.role;
    authMode = "on";
    wizardLoginError = "";
    render();
  } catch (e) {
    const msg = String(e);
    if (msg.includes("invalid_credentials")) {
      wizardLoginError = "Invalid email or password.";
    } else if (msg.includes("rate_limited")) {
      wizardLoginError = "Too many attempts — wait a minute and retry.";
    } else {
      wizardLoginError = msg;
    }
    render();
  }
}

async function finishOnboarding() {
  const baseInput = root.querySelector<HTMLInputElement>("#onboard-base");
  wizardBaseUrl = baseInput?.value.trim() ?? wizardBaseUrl;
  try {
    await invoke("complete_onboarding", {
      orchestratorBaseUrl: wizardBaseUrl,
      hotkey: settings.hotkey,
      libraryRoots: settings.libraryRoots,
      theme: currentTheme(),
    });
    wizardHealthStatus = "idle";
    wizardHealthMessage = "";
    wizardLoginError = "";
    await refreshSettingsFromRust();
    await refreshAuthFromRust();
    render();
    activateSummonHint(ctx);
  } catch (e) {
    alert(String(e));
  }
}

/** WDIO WebDriver webview: `listen()` can hang; event hooks are not needed for GUI E2E specs. */
async function listenOverlay(
  event: string,
  handler: Parameters<typeof listen>[1],
): Promise<UnlistenFn> {
  if (import.meta.env.VITE_WDIO_E2E === "true") {
    return async () => {};
  }
  return listen(event, handler);
}

let overlayEventListenersBound = false;

async function bindOverlayEventListeners(): Promise<void> {
  if (overlayEventListenersBound) {
    return;
  }
  overlayEventListenersBound = true;
  await listenOverlay("overlay-config-corrupt", (ev) => {
    const p = (ev.payload as { error?: string; path?: string }) || {};
    const ok = confirm(
      `overlay.json is corrupt or unsupported (${p.error ?? "error"}).\n\nReset to defaults? (A backup was written next to the file.)`,
    );
    if (ok) {
      void invoke("reset_overlay_config_to_defaults").then(() => location.reload());
    } else {
      void getCurrentWindow().close();
    }
  });
  await listenOverlay("hotkey-register-failed", (ev) => {
    alert(`Global hotkey registration failed: ${String(ev.payload)}`);
  });
  await listenOverlay("settings-saved", async () => {
    await refreshSettingsFromRust();
  });
  await listen("core-ready", async () => {
    if (!shouldShowOnboarding()) {
      await refreshAuthFromRust();
      render();
    }
  });
  await getCurrentWindow().onFocusChanged(({ payload: focused }) => {
    if (focused) {
      // Tray/hotkey can show the window while wizard markup is still mounted.
      if (
        !shouldShowOnboarding() &&
        (root.classList.contains("hub-setup-mode") || root.querySelector("#onboarding"))
      ) {
        root.classList.remove("hub-setup-mode");
        render();
      }
      focusSearchInput(root, settingsViewOpen);
    } else {
      dismissSummonHint(ctx);
    }
  });
}

async function applyOverlayBootState(firstBoot: boolean): Promise<void> {
  if (firstBoot) {
    profile = hooks.resolveProfile ? await hooks.resolveProfile() : await defaultResolveProfile();
  }

  try {
    await refreshSettingsFromRust();
    await hooks.prepareBoot?.(ctx);
    if (firstBoot) {
      unwatchSystemTheme?.();
      unwatchSystemTheme = watchSystemTheme(() => {
        if (currentTheme() === "system") applyTheme("system");
      });
    }
    render();
    if (firstBoot) {
      await hooks.onBoot?.(ctx);
    }
    if (!shouldShowOnboarding()) {
      if (import.meta.env.VITE_WDIO_E2E === "true") {
        // WDIO specs mock invoke; avoid blocking boot on live Core.
        authMode = (settings.authMode as AuthMode) || "on";
        sessionPresent = Boolean(settings.sessionPresent);
        sessionRole = settings.sessionRole ?? null;
      } else {
        const stackWarming = Boolean(hooks.startingBanner?.(ctx));
        if (!stackWarming) {
          await refreshAuthFromRust();
        }
      }
      render();
    }
    if (firstBoot && import.meta.env.VITE_WDIO_E2E !== "true") {
      await offerSummonHintIfPending(ctx);
      // LUM-455: independently offer the Wayland re-summon recovery hint (Rust-gated).
      await offerRecoveryHintIfNeeded(ctx);
    }
  } catch (e) {
    root.innerHTML = `<div class="banner err"><p>Lumogis failed to start.</p><p class="hint">${String(e)}</p></div>`;
  }
}

async function boot() {
  await bindOverlayEventListeners();
  await applyOverlayBootState(true);
}

/** WDIO mock-leg: reset UI state from mocked invoke without re-binding Tauri listeners. */
async function rebootForE2e(): Promise<void> {
  settingsViewOpen = false;
  adminSettings = null;
  selectedHitIndex = 0;
  if (lastController) {
    lastController.abort();
    lastController = null;
  }
  if (debounceTimer) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
  await applyOverlayBootState(false);
}

  return { boot, rebootForE2e };
}
