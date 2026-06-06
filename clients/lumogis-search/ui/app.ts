// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
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
import { createHitRow } from "./hitRow";
import {
  canManageIngestPaths,
  canUploadIngest,
  isSearchDisabled,
  needsOnboarding,
  onboardingMarkup,
  type AuthMode,
} from "./overlayUi";

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

/** Host-reported desktop profile (`get_desktop_profile`); literals are opaque to this crate. */
export type DesktopProfile = "client-only" | "bundled";

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

const LOGO_MARK_SRC = new URL("./assets/logo-mark.svg", import.meta.url).href;

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

let profile: DesktopProfile = "client-only";

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
    return p === "bundled" ? "bundled" : "client-only";
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

function ingestPathsEditorMarkup(): string {
  const paths = editorIngestPaths();
  const rows = paths
    .map(
      (p) =>
        `<div class="ingest-path-row">
          <input type="text" class="ingest-path-input" value="${escapeHtml(p)}" placeholder="Host path (e.g. ./lumogis-data)" />
          <button type="button" class="ingest-path-remove" title="Remove">×</button>
        </div>`,
    )
    .join("");
  const paperless = adminSettings?.paperlessConfigured
    ? `<span class="badge ok">Paperless connected</span>`
    : `<span class="badge muted">Paperless not configured</span>`;
  const restartBanner = adminSettings?.restartRequired
    ? `<div class="banner warn" id="restart-banner">Restart required for ingest path changes to take effect. New bind mounts need a Docker restart.</div>`
    : "";
  return `
    <section class="settings-section">
      <h2 class="settings-heading">Server ingest paths</h2>
      <p class="hint">Paths on the host where Lumogis watches and ingests files. Additional paths beyond the first may require extra volume mounts in Docker Compose.</p>
      ${paperless}
      ${restartBanner}
      <div id="ingest-paths-list">${rows}</div>
      <div class="toolbar">
        <input type="text" id="new-ingest-path" placeholder="Add path…" />
        <button type="button" id="btn-add-ingest-path">+ Add path</button>
      </div>
      <div class="toolbar">
        <button type="button" id="btn-save-ingest-paths">Save ingest paths</button>
        <button type="button" id="btn-restart-stack" ${adminSettings?.restartRequired ? "" : "disabled"}>Apply &amp; Restart</button>
      </div>
      <p class="hint" id="ingest-admin-hint"></p>
    </section>`;
}

function uploadSectionMarkup(): string {
  return `
    <section class="settings-section">
      <h2 class="settings-heading">Push upload</h2>
      <p class="hint">Upload a document to your personal ingest queue on the server.</p>
      <input type="file" id="ingest-upload-input" />
      <p class="hint" id="upload-hint"></p>
    </section>`;
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

function themeToggleMarkup(): string {
  const mode = currentTheme();
  const options: { id: ThemeMode; label: string }[] = [
    { id: "system", label: "System" },
    { id: "light", label: "Light" },
    { id: "dark", label: "Dark" },
  ];
  const buttons = options
    .map(
      (o) =>
        `<button type="button" class="theme-option" data-theme-value="${o.id}" aria-pressed="${mode === o.id}">${o.label}</button>`,
    )
    .join("");
  return `
    <label>Theme
      <div class="theme-toggle" role="group" aria-label="Theme">
        ${buttons}
      </div>
    </label>`;
}

function render() {
  if (needsOnboarding(settings)) {
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

  const rootsEmpty = settings.libraryRoots.length === 0;
  const searchDisabled = hooks.computeSearchDisabled
    ? hooks.computeSearchDisabled(ctx)
    : isSearchDisabled(needsLogin(), authMode);
  const startingBanner = hooks.startingBanner?.(ctx) ?? null;
  root.innerHTML = `
    <div class="toolbar">
      <input type="search" id="q" placeholder="Search memory…" autocomplete="off" ${searchDisabled ? "disabled" : ""} />
      <button type="button" id="btn-settings">Settings</button>
    </div>
    ${startingBanner ? `<div class="banner" id="starting-banner">${startingBanner}</div>` : ""}
    ${authMode === "unreachable" ? `<div class="banner err" id="auth-unreachable">Could not reach Lumogis — check the base URL and that the stack is running.</div>` : ""}
    ${needsLogin() ? `<div class="login-panel" id="login-panel">
      <p class="hint">Sign in to search your memory.</p>
      <label>Email <input id="login-email" type="email" autocomplete="username" /></label>
      <label>Password <input id="login-password" type="password" minlength="12" autocomplete="current-password" /></label>
      <div class="toolbar">
        <button type="button" class="primary" id="btn-login">Sign in</button>
      </div>
      <p class="hint err-text" id="login-error"></p>
    </div>` : ""}
    ${rootsEmpty ? `<div class="banner warn" id="roots-banner">Add library roots in Settings to open files locally.</div>` : ""}
    <p id="search-hint" class="hint search-empty-hint">Start typing to search your files…</p>
    <div id="degraded" class="banner warn" style="display:none"></div>
    <div id="error" class="banner err" style="display:none"></div>
    <div class="results" id="results"></div>
    <div class="settings-panel" id="settings">
      <div class="settings-brand">
        <img src="${LOGO_MARK_SRC}" width="28" height="28" alt="" />
        <span class="settings-wordmark">Lumogis</span>
      </div>
      ${themeToggleMarkup()}
      <label>Orchestrator base URL
        <input id="set-base" type="url" />
      </label>
      <label>Global hotkey
        <input id="set-hotkey" type="text" />
      </label>
      <p class="hint">Default <code>CommandOrControl+Shift+L</code>. Invalid values are not saved.</p>
      <label>Library roots (one directory per line)
        <textarea id="set-roots"></textarea>
      </label>
      <p class="hint">Open / Shift+click reveal use these <strong>local</strong> folders only — not server ingest paths.</p>
      ${canManageIngestPaths(authMode, sessionRole) && !hooks.hideAdminIngest?.(ctx) ? ingestPathsEditorMarkup() : ""}
      ${canUploadIngest(authMode, sessionPresent) ? uploadSectionMarkup() : ""}
      <p class="hint" id="session-hint"></p>
      <div class="toolbar">
        <button type="button" class="primary" id="btn-save">Save settings</button>
        <button type="button" id="btn-logout" ${authMode !== "on" || !sessionPresent ? "disabled" : ""}>Sign out</button>
        <button type="button" id="btn-close-settings">Back</button>
      </div>
      <p class="hint" id="keychain-hint"></p>
    </div>
  `;

  const q = root.querySelector<HTMLInputElement>("#q")!;
  const results = root.querySelector<HTMLDivElement>("#results")!;
  const degraded = root.querySelector<HTMLDivElement>("#degraded")!;
  const errBox = root.querySelector<HTMLDivElement>("#error")!;
  const panel = root.querySelector<HTMLDivElement>("#settings")!;

  applyTheme(currentTheme());
  updateQueryEmptyState(q.value);

  q.addEventListener("input", () => {
    updateQueryEmptyState(q.value);
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => void runSearch(q.value, results, degraded, errBox), 250);
  });

  root.querySelector("#btn-settings")!.addEventListener("click", () => {
    void openSettingsPanel();
  });
  root.querySelector("#btn-close-settings")!.addEventListener("click", () => {
    panel.classList.remove("open");
  });
  root.querySelector("#btn-save")!.addEventListener("click", () => void saveAllSettings(panel));
  const logoutBtn = root.querySelector<HTMLButtonElement>("#btn-logout");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", () => void signOut());
  }
  const loginBtn = root.querySelector<HTMLButtonElement>("#btn-login");
  if (loginBtn) {
    loginBtn.addEventListener("click", () => void submitLogin());
  }

  wireIngestPathsEditor(panel);
  wireUploadInput(panel);

  for (const btn of panel.querySelectorAll<HTMLButtonElement>(".theme-option")) {
    btn.addEventListener("click", () => {
      const value = normalizeTheme(btn.dataset.themeValue ?? "system");
      setThemeMode(value);
      for (const b of panel.querySelectorAll<HTMLButtonElement>(".theme-option")) {
        b.setAttribute("aria-pressed", String(b.dataset.themeValue === value));
      }
    });
  }

  void runSearch(q.value, results, degraded, errBox);
}

async function openSettingsPanel() {
  await loadAdminSettingsIfNeeded();
  render();
  root.querySelector<HTMLDivElement>("#settings")?.classList.add("open");
  fillSettingsForm();
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
      <input type="text" class="ingest-path-input" value="${escapeHtml(input.value.trim())}" placeholder="Host path" />
      <button type="button" class="ingest-path-remove" title="Remove">×</button>`;
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
    if (hint) hint.textContent = "Ingest paths saved. Restart when prompted if paths changed.";
    const restartBtn = panel.querySelector<HTMLButtonElement>("#btn-restart-stack");
    if (restartBtn) restartBtn.disabled = !adminSettings.restartRequired;
    const banner = panel.querySelector<HTMLDivElement>("#restart-banner");
    if (adminSettings.restartRequired && !banner) {
      const section = panel.querySelector(".settings-section");
      section?.insertAdjacentHTML(
        "beforeend",
        `<div class="banner warn" id="restart-banner">Restart required for ingest path changes to take effect.</div>`,
      );
    }
  } catch (e) {
    if (hint) hint.textContent = String(e);
  }
}

async function applyRestart(panel: HTMLElement) {
  const hint = panel.querySelector<HTMLParagraphElement>("#ingest-admin-hint");
  const ok = confirm(
    "Apply pending ingest path changes and restart the orchestrator stack?\n\nThis recreates containers (brief downtime).",
  );
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
  (root.querySelector("#set-base") as HTMLInputElement).value = settings.orchestratorBaseUrl;
  (root.querySelector("#set-hotkey") as HTMLInputElement).value = settings.hotkey;
  (root.querySelector("#set-roots") as HTMLTextAreaElement).value = settings.libraryRoots.join("\n");
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
  const probe = await invoke<AuthProbe>("probe_auth_state", {
    orchestratorBaseUrl: orchestratorBaseUrl ?? null,
  });
  authMode = (probe.mode as AuthMode) || "unknown";
  sessionPresent = Boolean(probe.sessionPresent);
  sessionRole = probe.role ?? null;
}

async function saveAllSettings(panel: HTMLElement) {
  const base = (root.querySelector("#set-base") as HTMLInputElement).value.trim();
  const hotkey = (root.querySelector("#set-hotkey") as HTMLInputElement).value.trim();
  const rootsRaw = (root.querySelector("#set-roots") as HTMLTextAreaElement).value;
  const roots = rootsRaw
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const theme = currentTheme();
  try {
    await invoke("save_overlay_settings", {
      orchestratorBaseUrl: base,
      hotkey,
      libraryRoots: roots,
      theme,
    });
    await refreshSettingsFromRust();
    await refreshAuthFromRust();
    panel.classList.remove("open");
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
  if (needsOnboarding(settings)) {
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
    for (const h of data.hits.slice(0, 5)) {
      results.appendChild(createHitRow(h, settings.libraryRoots));
    }
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
  } catch (e) {
    alert(String(e));
  }
}

async function boot() {
  await listen("overlay-config-corrupt", (ev) => {
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
  await listen("hotkey-register-failed", (ev) => {
    alert(`Global hotkey registration failed: ${String(ev.payload)}`);
  });
  await listen("settings-saved", async () => {
    await refreshSettingsFromRust();
  });

  // Resolve the desktop profile before the first render().
  profile = hooks.resolveProfile ? await hooks.resolveProfile() : await defaultResolveProfile();

  await refreshSettingsFromRust();
  if (!needsOnboarding(settings)) {
    await refreshAuthFromRust();
  }
  unwatchSystemTheme?.();
  unwatchSystemTheme = watchSystemTheme(() => {
    if (currentTheme() === "system") applyTheme("system");
  });
  render();
  await hooks.onBoot?.(ctx);
}

  return { boot };
}
