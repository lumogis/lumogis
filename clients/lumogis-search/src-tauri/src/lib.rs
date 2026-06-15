// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

//! Lumogis Search — Tauri 2 household memory search overlay (AGPL).

mod auth;
pub mod commands;
mod overlay_window;
pub mod system_tray;

pub use overlay_window::{enter_overlay_mode_inner, is_wayland_session};

use auth::AuthCoordinator;
use auth::AuthMode;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use tokio_util::sync::CancellationToken;

const DEFAULT_HOTKEY: &str = "CommandOrControl+Shift+L";
const DEFAULT_BASE_URL: &str = "http://127.0.0.1:8000";
const SCHEMA_VERSION: u32 = 3;
const MAX_SUPPORTED_SCHEMA_VERSION: u32 = 3;

/// Canonical set of the **shared** Tauri command names the frontend invokes.
/// Cross-client **frontend invoke-string** contract — it does NOT by itself
/// prove macro registration (`generate_handler!` registers by fn ident).
/// `get_desktop_profile` is intentionally **excluded**: each host binary
/// registers its own profile command (Search → `"client-only"`). Each binary's
/// `generate_handler!` therefore registers 1 (local profile fn) + 20 (these) = 21 commands.
pub const SHARED_COMMAND_NAMES: &[&str] = &[
    "get_overlay_settings",
    "save_overlay_settings",
    "validate_hotkey",
    "set_access_token",
    "read_session",
    "write_session",
    "clear_session",
    "probe_server_health",
    "probe_auth_state",
    "auth_login",
    "complete_onboarding",
    "fetch_admin_settings",
    "save_admin_ingest_paths",
    "restart_orchestrator_stack",
    "upload_ingest_file",
    "search_memory",
    "open_if_allowed",
    "reveal_if_allowed",
    "reset_overlay_config_to_defaults",
    "take_pending_summon_hint",
];

// ── Wire DTOs (FastAPI /api/v1/memory/search — snake_case JSON) ─────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MemorySearchHitDto {
    pub id: String,
    pub score: f64,
    pub title: Option<String>,
    pub snippet: String,
    pub source: Option<String>,
    pub created_at: Option<String>,
    #[serde(default = "default_scope")]
    pub scope: String,
    pub owner_user_id: Option<String>,
}

fn default_scope() -> String {
    "personal".into()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MemorySearchResponseDto {
    pub hits: Vec<MemorySearchHitDto>,
    #[serde(default)]
    pub degraded: bool,
    pub reason: Option<String>,
}

// ── overlay.json (camelCase on disk) ───────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OverlayConfig {
    pub schema_version: u32,
    pub orchestrator_base_url: String,
    pub hotkey: String,
    pub library_roots: Vec<String>,
    #[serde(default = "default_theme")]
    pub theme: String,
    #[serde(default)]
    pub onboarding_complete: bool,
}

fn default_theme() -> String {
    "system".into()
}

fn validate_theme(theme: &str) -> Result<(), String> {
    match theme {
        "system" | "light" | "dark" => Ok(()),
        other => Err(format!("invalid_theme:{other}")),
    }
}

impl Default for OverlayConfig {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            orchestrator_base_url: "http://127.0.0.1:8000".into(),
            hotkey: DEFAULT_HOTKEY.into(),
            library_roots: vec![],
            theme: default_theme(),
            onboarding_complete: false,
        }
    }
}

/// v1→v2 upgrade heuristic (LUM-398) — keychain session is supplied by caller after `AppState` exists.
pub fn onboarding_complete_for(
    roots_nonempty: bool,
    session_present_for_host: bool,
    base_url: &str,
    default_url: &str,
) -> bool {
    roots_nonempty || session_present_for_host || base_url != default_url
}

fn apply_onboarding_migration(
    cfg: &mut OverlayConfig,
    session_present_for_host: bool,
    bundled_track: bool,
) -> bool {
    let mut changed = false;
    if bundled_track && cfg.library_roots.is_empty() && cfg.onboarding_complete {
        cfg.onboarding_complete = false;
        changed = true;
    }
    // Hub bundled: v2 incorrectly auto-marked onboarding complete when library roots
    // were saved mid-wizard — force the setup wizard until complete_onboarding runs.
    if bundled_track && cfg.schema_version < 3 {
        cfg.onboarding_complete = false;
        cfg.schema_version = 3;
        changed = true;
    }
    if !cfg.onboarding_complete {
        let complete = if bundled_track {
            session_present_for_host
        } else {
            onboarding_complete_for(
                !cfg.library_roots.is_empty(),
                session_present_for_host,
                &cfg.orchestrator_base_url,
                DEFAULT_BASE_URL,
            )
        };
        if complete {
            cfg.onboarding_complete = true;
            changed = true;
        }
    }
    if cfg.schema_version < SCHEMA_VERSION {
        cfg.schema_version = SCHEMA_VERSION;
        changed = true;
    }
    changed
}

pub struct AppState {
    pub inner: Mutex<AppStateInner>,
}

pub struct AppStateInner {
    pub config: OverlayConfig,
    pub config_path: PathBuf,
    /// Cancels in-flight search when a new search starts (AbortController analogue).
    pub search_root: CancellationToken,
    pub last_toggle: Option<Instant>,
    pub auth_mode: AuthMode,
    pub auth: AuthCoordinator,
    /// Blur-dismiss handler attached at most once (LUM-446).
    pub overlay_dismiss_wired: bool,
    /// Ignore blur-dismiss until this instant (chrome handoff / show-once grace).
    pub overlay_dismiss_suppress_until: Option<Instant>,
    /// Consumed by `take_pending_summon_hint` after show-once overlay reveal (LUM-456).
    pub pending_summon_hint: bool,
}

// ── Path allowlist (C5) ─────────────────────────────────────────────────────

/// Returns true when `canonical_target` is exactly a root or a strict child under a root.
pub fn is_path_allowed(canonical_target: &Path, canonical_roots: &[PathBuf]) -> bool {
    for root in canonical_roots {
        if canonical_target == root {
            return true;
        }
        if let Ok(rest) = canonical_target.strip_prefix(root) {
            if rest.components().next().is_some() {
                return true;
            }
        }
    }
    false
}

fn load_overlay_json(path: &Path) -> Result<OverlayConfig, String> {
    let raw = fs::read_to_string(path).map_err(|e| e.to_string())?;
    let cfg: OverlayConfig = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    if cfg.schema_version == 0 || cfg.schema_version > MAX_SUPPORTED_SCHEMA_VERSION {
        return Err(format!(
            "unsupported_schema_version:{}",
            cfg.schema_version
        ));
    }
    let theme = if validate_theme(&cfg.theme).is_ok() {
        cfg.theme
    } else {
        default_theme()
    };
    Ok(OverlayConfig {
        orchestrator_base_url: auth::normalise_base_url(cfg.orchestrator_base_url),
        theme,
        ..cfg
    })
}

fn save_overlay_json(path: &Path, cfg: &OverlayConfig) -> Result<(), String> {
    let tmp = path.with_extension("json.tmp");
    let json = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    fs::write(&tmp, json).map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

fn backup_corrupt(path: &Path, raw: &str) -> Result<(), String> {
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let bak = path.with_extension(format!("json.bak.{ts}"));
    fs::write(&bak, raw).map_err(|e| e.to_string())
}

/// Show/focus overlay `main` window (tray menu Show, recovery after handoff).
pub fn show_overlay_window(app: &AppHandle) {
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let state = app.state::<AppState>();
    let onboarding_complete = {
        let g = state.inner.lock().expect("state poisoned");
        g.config.onboarding_complete
    };
    if onboarding_complete {
        let _ = overlay_window::apply_overlay_chrome(&win);
        let _ = overlay_window::attach_overlay_dismiss(app, &win);
        reregister_hotkey_best_effort(app);
        overlay_window::suppress_overlay_dismiss_briefly(app);
    }
    let _ = win.show();
    let _ = win.set_focus();
}

/// Debounced show/hide/focus for overlay `main` window (hotkey + tray left-click).
pub fn toggle_overlay_window(app: &AppHandle) {
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let state = app.state::<AppState>();
    {
        let mut g = state.inner.lock().expect("state poisoned");
        let now = Instant::now();
        if let Some(last) = g.last_toggle {
            if now.duration_since(last) < Duration::from_millis(100) {
                return;
            }
        }
        g.last_toggle = Some(now);
    }
    let onboarding_complete = {
        let g = state.inner.lock().expect("state poisoned");
        g.config.onboarding_complete
    };
    let visible = win.is_visible().unwrap_or(false);
    let focused = win.is_focused().unwrap_or(false);
    // GTK may report visible while the operator sees nothing after chrome handoff —
    // treat unfocused as summon, not dismiss.
    if visible && focused {
        let _ = win.hide();
    } else if onboarding_complete {
        show_overlay_window(app);
    } else {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn reregister_hotkey(app: &AppHandle) -> Result<(), String> {
    let hotkey_str = {
        let state = app.state::<AppState>();
        let g = state.inner.lock().expect("state poisoned");
        g.config.hotkey.clone()
    };
    let gs = app.global_shortcut();
    gs.unregister_all().map_err(|e| e.to_string())?;
    let hk: Shortcut = hotkey_str
        .parse()
        .map_err(|_| format!("invalid_hotkey:{hotkey_str}"))?;
    gs.on_shortcut(hk, move |ah, _shortcut, event| {
        if event.state() != ShortcutState::Pressed {
            return;
        }
        toggle_overlay_window(ah);
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// Persisted settings must not fail when the OS hotkey is already taken (e.g. zombie Hub).
pub fn reregister_hotkey_best_effort(app: &AppHandle) {
    if let Err(e) = reregister_hotkey(app) {
        tracing::warn!("global shortcut registration failed: {e}");
        let _ = app.emit("hotkey-register-failed", e);
    }
}

// ── Commands ────────────────────────────────────────────────────────────────
//
// The 19 **shared** commands now live in the `commands` submodule (see
// `SHARED_COMMAND_NAMES`) — `#[tauri::command] pub fn` cannot be defined in the
// crate root (`lib.rs`/`main.rs`). They are registered below as `commands::<name>`.
//
// `get_desktop_profile` is the one **per-binary** command and stays at the crate
// root: Search reports `"client-only"`; embedding binaries may supply another value.

#[tauri::command]
fn get_desktop_profile() -> String {
    "client-only".into()
}

/// Shared Tauri builder seam: the `Builder` with the plugins every Lumogis
/// desktop client needs (opener + global shortcut). Embedding callers may add
/// extra plugins before `.setup(...)`.
pub fn shared_builder() -> tauri::Builder<tauri::Wry> {
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build());
    #[cfg(feature = "wdio-e2e")]
    {
        builder = builder
            .plugin(tauri_plugin_wdio::init())
            .plugin(tauri_plugin_wdio_webdriver::init());
    }
    builder
}

/// Shared Tauri setup seam: resolve `app_config_dir`, load/migrate `overlay.json`,
/// build & `manage` `AppState`, register the hotkey, wire focus-hide, emit
/// corrupt/hotkey events.
///
/// `base_url_override`: when `Some`, the loaded config's orchestrator base URL is
/// replaced by the host (e.g. a fixed local Core URL). When `None` (Search default)
/// the override branch is skipped — behaviour is byte-identical to standalone Search.
pub fn shared_setup(
    app: &tauri::AppHandle,
    base_url_override: Option<String>,
    hide_on_focus_loss: bool,
    bundled_track: bool,
    tray_config: system_tray::TrayConfig,
    tray_menu: system_tray::TrayMenuSpec,
    tray_click: system_tray::TrayClickBehavior,
    tray_tooltip: &str,
    install_tray: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let resolver = app.path();
    let dir = resolver
        .app_config_dir()
        .map_err(|e| -> Box<dyn std::error::Error> { Box::new(e) })?;
    fs::create_dir_all(&dir).map_err(|e| -> Box<dyn std::error::Error> { Box::new(e) })?;
    let config_path = dir.join("overlay.json");
    let (mut config, corrupt) = if config_path.exists() {
        let raw = fs::read_to_string(&config_path).unwrap_or_default();
        match load_overlay_json(&config_path) {
            Ok(c) => (c, None),
            Err(e) => {
                let _ = backup_corrupt(&config_path, &raw);
                (OverlayConfig::default(), Some((e, config_path.clone())))
            }
        }
    } else {
        let c = OverlayConfig::default();
        save_overlay_json(&config_path, &c)?;
        (c, None)
    };
    if let Some(url) = base_url_override {
        config.orchestrator_base_url = auth::normalise_base_url(url);
    }
    let session_present = {
        let host = auth::host_for_keyring(&config.orchestrator_base_url);
        auth::read_session(&host)
            .ok()
            .flatten()
            .is_some()
    };
    let migrated = apply_onboarding_migration(&mut config, session_present, bundled_track);
    if migrated {
        save_overlay_json(&config_path, &config)?;
    }
    let state = AppState {
        inner: Mutex::new(AppStateInner {
            config,
            config_path,
            search_root: CancellationToken::new(),
            last_toggle: None,
            auth_mode: AuthMode::Unknown,
            auth: AuthCoordinator::default(),
            overlay_dismiss_wired: false,
            overlay_dismiss_suppress_until: None,
            pending_summon_hint: false,
        }),
    };
    app.manage(state);
    if let Some((err, p)) = corrupt {
        let _ = app.emit(
            "overlay-config-corrupt",
            serde_json::json!({ "error": err, "path": p.to_string_lossy() }),
        );
    }
    reregister_hotkey_best_effort(app);
    if hide_on_focus_loss {
        let win = app.get_webview_window("main").expect("main window");
        overlay_window::attach_overlay_dismiss(app, &win)
            .map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
    }
    if install_tray {
        system_tray::install_system_tray(app, tray_config, tray_menu, tray_click, tray_tooltip);
    }
    system_tray::attach_close_requested_policy(app, tray_config);
    #[cfg(feature = "wdio-e2e")]
    {
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.show();
            let _ = win.set_focus();
        }
    }
    Ok(())
}

#[cfg(feature = "standalone-app")]
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    shared_builder()
        .setup(|app| {
            shared_setup(
                app.handle(),
                None,
                true,
                false,
                system_tray::TrayConfig::search_default(),
                system_tray::TrayMenuSpec::Hub,
                system_tray::TrayClickBehavior::ToggleOverlay,
                system_tray::TRAY_TOOLTIP,
                true,
            )
        })
        .invoke_handler(tauri::generate_handler![
            get_desktop_profile,
            commands::get_overlay_settings,
            commands::save_overlay_settings,
            commands::validate_hotkey,
            commands::set_access_token,
            commands::read_session,
            commands::write_session,
            commands::clear_session,
            commands::probe_server_health,
            commands::probe_auth_state,
            commands::auth_login,
            commands::complete_onboarding,
            commands::fetch_admin_settings,
            commands::save_admin_ingest_paths,
            commands::restart_orchestrator_stack,
            commands::upload_ingest_file,
            commands::search_memory,
            commands::open_if_allowed,
            commands::reveal_if_allowed,
            commands::reset_overlay_config_to_defaults,
            commands::take_pending_summon_hint,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, _event| {});
}

#[cfg(test)]
mod overlay_schema_tests {
    use super::*;

    #[test]
    fn onboarding_complete_for_existing_user() {
        assert!(onboarding_complete_for(true, false, DEFAULT_BASE_URL, DEFAULT_BASE_URL));
        assert!(onboarding_complete_for(
            false,
            true,
            DEFAULT_BASE_URL,
            DEFAULT_BASE_URL
        ));
        assert!(onboarding_complete_for(
            false,
            false,
            "https://lumogis.lan",
            DEFAULT_BASE_URL
        ));
    }

    #[test]
    fn onboarding_complete_for_fresh_install() {
        assert!(!onboarding_complete_for(
            false,
            false,
            DEFAULT_BASE_URL,
            DEFAULT_BASE_URL
        ));
    }

    #[test]
    fn load_overlay_v1_fresh_migrates_onboarding_false() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("overlay.json");
        let raw = r#"{
            "schemaVersion": 1,
            "orchestratorBaseUrl": "http://127.0.0.1:8000",
            "hotkey": "CommandOrControl+Shift+L",
            "libraryRoots": [],
            "theme": "system"
        }"#;
        fs::write(&path, raw).unwrap();
        let cfg = load_overlay_json(&path).unwrap();
        assert!(!cfg.onboarding_complete);
        assert_eq!(cfg.schema_version, 1);
    }

    #[test]
    fn load_overlay_v2_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("overlay.json");
        let cfg = OverlayConfig {
            schema_version: 2,
            orchestrator_base_url: "https://house.lan".into(),
            hotkey: DEFAULT_HOTKEY.into(),
            library_roots: vec![],
            theme: "dark".into(),
            onboarding_complete: true,
        };
        save_overlay_json(&path, &cfg).unwrap();
        let loaded = load_overlay_json(&path).unwrap();
        assert!(loaded.onboarding_complete);
        assert_eq!(loaded.schema_version, 2);
        assert_eq!(loaded.theme, "dark");
    }
}

#[cfg(test)]
mod path_tests {
    use super::*;
    #[cfg(unix)]
    use std::os::unix::fs::symlink;

    #[test]
    fn prefix_child_allowed() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("lib");
        fs::create_dir_all(&root).unwrap();
        let child = root.join("a").join("b.txt");
        fs::create_dir_all(child.parent().unwrap()).unwrap();
        fs::write(&child, b"x").unwrap();
        let cr = root.canonicalize().unwrap();
        let ct = child.canonicalize().unwrap();
        assert!(is_path_allowed(&ct, &[cr]));
    }

    #[test]
    #[cfg(unix)]
    fn symlink_escape_blocked() {
        let tmp = tempfile::tempdir().unwrap();
        let jail = tmp.path().join("jail");
        fs::create_dir_all(&jail).unwrap();
        let evil = jail.join("evil");
        symlink("/etc", &evil).unwrap();
        let root = jail.canonicalize().unwrap();
        let target = evil.canonicalize().unwrap();
        assert!(!is_path_allowed(&target, &[root]));
    }
}

#[cfg(test)]
mod command_registry_tests {
    use super::*;
    use std::collections::HashSet;

    /// Frontend-contract guard (LUM-435 Chunk A): the shared command-name set is
    /// exactly 20 with no duplicates. Search's `generate_handler!` registers these
    /// 20 plus the local `get_desktop_profile` = 21 total. This guards the
    /// frontend invoke-string contract, not macro registration.
    #[test]
    fn shared_command_names_count_and_unique() {
        assert_eq!(SHARED_COMMAND_NAMES.len(), 20, "shared command count drifted");
        let unique: HashSet<&&str> = SHARED_COMMAND_NAMES.iter().collect();
        assert_eq!(
            unique.len(),
            SHARED_COMMAND_NAMES.len(),
            "duplicate command name in SHARED_COMMAND_NAMES"
        );
        assert!(
            !SHARED_COMMAND_NAMES.contains(&"enter_overlay_mode"),
            "enter_overlay_mode is Hub-only; must not appear in shared command names"
        );
    }
}
