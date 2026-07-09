// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

//! Shared Tauri commands.
//!
//! These are the 22 commands listed in [`super::SHARED_COMMAND_NAMES`] — the
//! cross-client contract consumed by Search's `run()` and other embedding binaries.
//! They live in this submodule rather than the crate root because
//! `#[tauri::command]` on a `pub fn` cannot be defined in `lib.rs`/`main.rs`
//! (the macro emits a re-export that collides at the crate root — a documented
//! Tauri limitation). Callers reference them as `commands::<name>` in
//! `generate_handler!`; cross-crate consumers use
//! `lumogis_search_lib::commands::<name>`. The JS `invoke("<name>")` string is
//! unchanged (the `commands::` prefix is ignored by Tauri).
//!
//! The per-binary `get_desktop_profile` command stays in the crate root, since
//! each binary supplies its own profile value (Search → "client-only").

use super::auth::{
    self, AdminSettingsPublic, AuthCoordinator, AuthMode, AuthProbeResult, AuthSession,
    AuthSessionPublic, IngestUploadQueuedPublic,
};
use super::{
    is_path_allowed, reregister_hotkey_best_effort, save_overlay_json, validate_theme, AppState,
    MemorySearchResponseDto, OverlayConfig, SCHEMA_VERSION,
};
use serde::Serialize;
use std::path::PathBuf;
use tauri::{AppHandle, Emitter};
use tauri_plugin_global_shortcut::Shortcut;
use tauri_plugin_opener::OpenerExt;
use tokio_util::sync::CancellationToken;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverlaySettingsPayload {
    pub schema_version: u32,
    pub orchestrator_base_url: String,
    pub hotkey: String,
    pub library_roots: Vec<String>,
    pub theme: String,
    pub keychain_error: Option<String>,
    pub auth_mode: String,
    pub session_present: bool,
    pub session_role: Option<String>,
    pub onboarding_complete: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerHealthProbeResult {
    pub status: String,
    pub http_status: Option<u16>,
    pub message: Option<String>,
}

#[tauri::command]
pub fn get_overlay_settings(state: tauri::State<'_, AppState>) -> Result<OverlaySettingsPayload, String> {
    let g = state.inner.lock().expect("state poisoned");
    let host = auth::host_for_keyring(&g.config.orchestrator_base_url);
    let keychain_error = auth::read_session(&host).err().map(|e| e.to_string());
    let session = auth::session_public(&host).ok();
    Ok(OverlaySettingsPayload {
        schema_version: g.config.schema_version,
        orchestrator_base_url: g.config.orchestrator_base_url.clone(),
        hotkey: g.config.hotkey.clone(),
        library_roots: g.config.library_roots.clone(),
        theme: g.config.theme.clone(),
        keychain_error,
        auth_mode: match g.auth_mode {
            AuthMode::Off => "off".into(),
            AuthMode::On => "on".into(),
            AuthMode::Unreachable => "unreachable".into(),
            AuthMode::Unknown => "unknown".into(),
        },
        session_present: session.as_ref().map(|s| s.present).unwrap_or(false),
        session_role: session.filter(|s| s.present).map(|s| s.role),
        onboarding_complete: g.config.onboarding_complete,
    })
}

#[tauri::command]
pub fn validate_hotkey(hotkey: String) -> Result<(), String> {
    let _: Shortcut = hotkey
        .parse()
        .map_err(|_| format!("invalid_hotkey:{hotkey}"))?;
    Ok(())
}

#[tauri::command]
pub fn save_overlay_settings(
    app: AppHandle,
    state: tauri::State<'_, AppState>,
    orchestrator_base_url: String,
    hotkey: String,
    library_roots: Vec<String>,
    theme: String,
) -> Result<(), String> {
    validate_hotkey(hotkey.clone())?;
    validate_theme(&theme)?;
    let base = auth::normalise_base_url(orchestrator_base_url);
    let mut g = state.inner.lock().expect("state poisoned");
    g.config.orchestrator_base_url = base;
    g.config.hotkey = hotkey;
    g.config.library_roots = library_roots;
    g.config.theme = theme;
    save_overlay_json(&g.config_path, &g.config)?;
    drop(g);
    reregister_hotkey_best_effort(&app);
    let _ = app.emit("settings-saved", ());
    Ok(())
}

/// Dev-only emergency path — prefer `auth_login` in production (see README).
#[tauri::command]
pub fn set_access_token(state: tauri::State<'_, AppState>, token: String) -> Result<(), String> {
    let host = {
        let g = state.inner.lock().expect("state poisoned");
        auth::host_for_keyring(&g.config.orchestrator_base_url)
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    let session = AuthSession {
        access_token: token,
        refresh_token: String::new(),
        expires_at_unix: now + 3600,
        role: "user".into(),
    };
    auth::write_session(&host, &session)
}

#[tauri::command]
pub fn read_session(state: tauri::State<'_, AppState>) -> Result<AuthSessionPublic, String> {
    let host = {
        let g = state.inner.lock().expect("state poisoned");
        auth::host_for_keyring(&g.config.orchestrator_base_url)
    };
    auth::session_public(&host)
}

#[tauri::command]
pub fn write_session(state: tauri::State<'_, AppState>, session: AuthSession) -> Result<(), String> {
    let host = {
        let g = state.inner.lock().expect("state poisoned");
        auth::host_for_keyring(&g.config.orchestrator_base_url)
    };
    auth::write_session(&host, &session)
}

#[tauri::command]
pub fn clear_session(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let host = {
        let g = state.inner.lock().expect("state poisoned");
        auth::host_for_keyring(&g.config.orchestrator_base_url)
    };
    auth::clear_session(&host)
}

#[tauri::command]
pub async fn probe_server_health(orchestrator_base_url: String) -> Result<ServerHealthProbeResult, String> {
    let base = auth::normalise_base_url(orchestrator_base_url);
    let url = format!("{base}/healthz");
    let client = auth::http_client()?;
    let resp = match client.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            let msg = if e.is_timeout() || e.is_connect() {
                Some(
                    if e.is_timeout() {
                        "timeout".into()
                    } else {
                        "connection_failed".into()
                    },
                )
            } else {
                Some(e.to_string())
            };
            return Ok(ServerHealthProbeResult {
                status: "unreachable".into(),
                http_status: None,
                message: msg,
            });
        }
    };
    let http_status = resp.status().as_u16();
    if resp.status().is_success() {
        let body: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
        if body.get("status").and_then(|v| v.as_str()).is_some() {
            return Ok(ServerHealthProbeResult {
                status: "ok".into(),
                http_status: Some(http_status),
                message: None,
            });
        }
        return Ok(ServerHealthProbeResult {
            status: "degraded".into(),
            http_status: Some(http_status),
            message: Some("unexpected_healthz_shape".into()),
        });
    }
    Ok(ServerHealthProbeResult {
        status: "degraded".into(),
        http_status: Some(http_status),
        message: Some(format!("http_{http_status}")),
    })
}

#[tauri::command]
pub async fn probe_auth_state(
    state: tauri::State<'_, AppState>,
    orchestrator_base_url: Option<String>,
) -> Result<AuthProbeResult, String> {
    let use_saved_config = orchestrator_base_url.is_none();
    let base = match orchestrator_base_url {
        Some(url) => auth::normalise_base_url(url),
        None => {
            let g = state.inner.lock().expect("state poisoned");
            g.config.orchestrator_base_url.clone()
        }
    };
    let probe = auth::probe_auth(&base).await?;
    if use_saved_config {
        let mode = match probe.mode.as_str() {
            "off" => AuthMode::Off,
            "on" => AuthMode::On,
            "unreachable" => AuthMode::Unreachable,
            _ => AuthMode::Unknown,
        };
        let mut g = state.inner.lock().expect("state poisoned");
        g.auth_mode = mode;
    }
    Ok(probe)
}

#[tauri::command]
pub async fn auth_login(
    state: tauri::State<'_, AppState>,
    email: String,
    password: String,
    orchestrator_base_url: Option<String>,
) -> Result<AuthSessionPublic, String> {
    let use_saved_config = orchestrator_base_url.is_none();
    let base = match orchestrator_base_url {
        Some(url) => auth::normalise_base_url(url),
        None => {
            let g = state.inner.lock().expect("state poisoned");
            g.config.orchestrator_base_url.clone()
        }
    };
    let session = auth::login(&base, email, password).await?;
    if use_saved_config {
        let mut g = state.inner.lock().expect("state poisoned");
        g.auth_mode = AuthMode::On;
    }
    Ok(AuthSessionPublic {
        role: session.role,
        expires_at_unix: session.expires_at_unix,
        present: true,
    })
}

#[tauri::command]
pub fn complete_onboarding(
    app: AppHandle,
    state: tauri::State<'_, AppState>,
    orchestrator_base_url: String,
    hotkey: String,
    library_roots: Vec<String>,
    theme: String,
) -> Result<(), String> {
    validate_hotkey(hotkey.clone())?;
    validate_theme(&theme)?;
    let mut g = state.inner.lock().expect("state poisoned");
    g.config.orchestrator_base_url = auth::normalise_base_url(orchestrator_base_url);
    g.config.hotkey = hotkey;
    g.config.library_roots = library_roots;
    g.config.theme = theme;
    g.config.onboarding_complete = true;
    g.config.schema_version = SCHEMA_VERSION;
    save_overlay_json(&g.config_path, &g.config)?;
    drop(g);
    reregister_hotkey_best_effort(&app);
    let _ = app.emit("settings-saved", ());
    Ok(())
}

fn app_auth_context(state: &tauri::State<'_, AppState>) -> (String, AuthMode, AuthCoordinator) {
    let g = state.inner.lock().expect("state poisoned");
    (
        g.config.orchestrator_base_url.clone(),
        g.auth_mode,
        g.auth.clone(),
    )
}

#[tauri::command]
pub async fn fetch_admin_settings(
    state: tauri::State<'_, AppState>,
) -> Result<AdminSettingsPublic, String> {
    let (base, auth_mode, coordinator) = app_auth_context(&state);
    auth::fetch_admin_settings(&base, auth_mode, &coordinator).await
}

#[tauri::command]
pub async fn save_admin_ingest_paths(
    state: tauri::State<'_, AppState>,
    ingest_paths: Vec<String>,
) -> Result<AdminSettingsPublic, String> {
    let (base, auth_mode, coordinator) = app_auth_context(&state);
    auth::put_admin_ingest_paths(&base, auth_mode, &coordinator, ingest_paths).await
}

#[tauri::command]
pub async fn restart_orchestrator_stack(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let (base, auth_mode, coordinator) = app_auth_context(&state);
    auth::restart_orchestrator_stack(&base, auth_mode, &coordinator).await
}

#[tauri::command]
pub async fn upload_ingest_file(
    state: tauri::State<'_, AppState>,
    file_name: String,
    bytes: Vec<u8>,
) -> Result<IngestUploadQueuedPublic, String> {
    let (base, auth_mode, coordinator) = app_auth_context(&state);
    auth::upload_ingest_file(&base, auth_mode, &coordinator, file_name, bytes).await
}

#[tauri::command]
pub async fn search_memory(
    state: tauri::State<'_, AppState>,
    q: String,
) -> Result<MemorySearchResponseDto, String> {
    let q = q.trim().to_string();
    if q.is_empty() {
        return Err("empty_query".into());
    }
    let (base, auth_mode, coordinator, run_token) = {
        let mut g = state.inner.lock().expect("state poisoned");
        g.search_root.cancel();
        g.search_root = CancellationToken::new();
        let run_token = g.search_root.child_token();
        let base = g.config.orchestrator_base_url.clone();
        let auth_mode = g.auth_mode;
        let coordinator = g.auth.clone();
        (base, auth_mode, coordinator, run_token)
    };
    if auth_mode == AuthMode::On {
        let host = auth::host_for_keyring(&base);
        if auth::read_session(&host)?.is_none() {
            return Err("session_expired".into());
        }
    }
    auth::authenticated_memory_search(&base, &q, auth_mode, &coordinator, run_token).await
}

#[tauri::command]
pub fn open_if_allowed(app: AppHandle, state: tauri::State<'_, AppState>, path: String) -> Result<(), String> {
    let pb = PathBuf::from(path);
    let roots = {
        let g = state.inner.lock().expect("state poisoned");
        if g.config.library_roots.is_empty() {
            return Err("library_roots_empty".into());
        }
        g.config.library_roots.clone()
    };
    let mut canon_roots = Vec::new();
    for r in roots {
        if let Ok(c) = PathBuf::from(&r).canonicalize() {
            canon_roots.push(c);
        }
    }
    if canon_roots.is_empty() {
        return Err("library_roots_unresolvable".into());
    }
    let target = pb.canonicalize().map_err(|e| e.to_string())?;
    if !is_path_allowed(&target, &canon_roots) {
        return Err("unauthorized_path".into());
    }
    app.opener()
        .open_path(target.to_string_lossy(), None::<&str>)
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn reveal_if_allowed(app: AppHandle, state: tauri::State<'_, AppState>, path: String) -> Result<(), String> {
    let pb = PathBuf::from(path);
    let roots = {
        let g = state.inner.lock().expect("state poisoned");
        if g.config.library_roots.is_empty() {
            return Err("library_roots_empty".into());
        }
        g.config.library_roots.clone()
    };
    let mut canon_roots = Vec::new();
    for r in roots {
        if let Ok(c) = PathBuf::from(&r).canonicalize() {
            canon_roots.push(c);
        }
    }
    if canon_roots.is_empty() {
        return Err("library_roots_unresolvable".into());
    }
    let target = pb.canonicalize().map_err(|e| e.to_string())?;
    if !is_path_allowed(&target, &canon_roots) {
        return Err("unauthorized_path".into());
    }
    app.opener()
        .reveal_item_in_dir(&target)
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn take_pending_summon_hint(state: tauri::State<'_, AppState>) -> Result<bool, String> {
    let mut g = state.inner.lock().map_err(|_| "state poisoned".to_string())?;
    if g.pending_summon_hint {
        g.pending_summon_hint = false;
        Ok(true)
    } else {
        Ok(false)
    }
}

/// LUM-455 — Wayland recovery state for the overlay UI's DE-tailored re-summon hint.
/// `recovery_confirmed` (Rust) is the authoritative cross-launch gate for that hint.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SummonRecoveryState {
    pub wayland: bool,
    pub desktop: String, // "gnome" | "kde" | "other"
    pub recovery_confirmed: bool,
    pub show_once_opt_out: bool,
}

#[tauri::command]
pub fn get_summon_recovery_state(
    state: tauri::State<'_, AppState>,
) -> Result<SummonRecoveryState, String> {
    let g = state.inner.lock().map_err(|_| "state poisoned".to_string())?;
    let desktop = super::summon::detect_desktop_env(
        std::env::var("XDG_CURRENT_DESKTOP").ok().as_deref(),
    )
    .as_str()
    .to_string();
    Ok(SummonRecoveryState {
        wayland: super::is_wayland_session(),
        desktop,
        recovery_confirmed: g.config.recovery_confirmed,
        show_once_opt_out: g.config.show_once_opt_out,
    })
}

/// LUM-455 — user opted out of the Wayland show-once safety net ("Don't show on
/// startup"). The only user action that persistently retires show-once + the hint.
#[tauri::command]
pub fn set_show_once_opt_out(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut g = state.inner.lock().map_err(|_| "state poisoned".to_string())?;
    g.config.show_once_opt_out = true;
    save_overlay_json(&g.config_path, &g.config)
}

#[tauri::command]
pub fn reset_overlay_config_to_defaults(app: AppHandle, state: tauri::State<'_, AppState>) -> Result<(), String> {
    {
        let mut g = state.inner.lock().expect("state poisoned");
        g.config = OverlayConfig::default();
        save_overlay_json(&g.config_path, &g.config)?;
    }
    reregister_hotkey_best_effort(&app);
    let path = {
        let g = state.inner.lock().expect("state poisoned");
        g.config_path.to_string_lossy().to_string()
    };
    let _ = app.emit("overlay-reset", path);
    Ok(())
}
