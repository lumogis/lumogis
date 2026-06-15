// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

//! Overlay window chrome and blur-dismiss helpers (LUM-446 / shared AGPL seam).

use crate::AppState;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, WebviewWindow};

const OVERLAY_DISMISS_SUPPRESS_MS: u64 = 500;

/// Pure descriptor matching Search `tauri.conf.json` overlay profile (unit-test seam).
pub fn overlay_chrome_descriptor() -> (bool, bool, bool, bool) {
    (false, true, true, false)
}

/// Whether to attach blur-dismiss handler (idempotent guard).
pub fn should_attach_dismiss(already_wired: bool) -> bool {
    !already_wired
}

/// True when `WAYLAND_DISPLAY` is set (Linux Wayland session).
pub fn is_wayland_session() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some()
}

pub fn apply_overlay_chrome(win: &WebviewWindow) -> Result<(), String> {
    let (decorations, always_on_top, skip_taskbar, resizable) = overlay_chrome_descriptor();
    win.set_decorations(decorations)
        .map_err(|e| format!("set_decorations:{e}"))?;
    win.set_always_on_top(always_on_top)
        .map_err(|e| format!("set_always_on_top:{e}"))?;
    win.set_skip_taskbar(skip_taskbar)
        .map_err(|e| format!("set_skip_taskbar:{e}"))?;
    win.set_resizable(resizable)
        .map_err(|e| format!("set_resizable:{e}"))?;
    Ok(())
}

pub fn suppress_overlay_dismiss_briefly(app: &AppHandle) {
    let state = app.state::<AppState>();
    let Ok(mut g) = state.inner.lock() else {
        return;
    };
    g.overlay_dismiss_suppress_until =
        Some(Instant::now() + Duration::from_millis(OVERLAY_DISMISS_SUPPRESS_MS));
}

fn overlay_dismiss_suppressed(app: &AppHandle) -> bool {
    let state = app.state::<AppState>();
    let Ok(g) = state.inner.lock() else {
        return false;
    };
    let suppressed = g
        .overlay_dismiss_suppress_until
        .is_some_and(|until| Instant::now() < until);
    suppressed
}

pub fn attach_overlay_dismiss(app: &AppHandle, win: &WebviewWindow) -> Result<(), String> {
    let state = app.state::<AppState>();
    let mut g = state.inner.lock().map_err(|_| "state poisoned".to_string())?;
    if !should_attach_dismiss(g.overlay_dismiss_wired) {
        return Ok(());
    }
    g.overlay_dismiss_wired = true;
    drop(g);

    let app2 = app.clone();
    let win2 = win.clone();
    win.on_window_event(move |ev| {
        if let tauri::WindowEvent::Focused(false) = ev {
            if overlay_dismiss_suppressed(&app2) {
                return;
            }
            let _ = win2.hide();
        }
    });
    Ok(())
}

/// Apply overlay chrome, wire blur-dismiss, refresh hotkey, then show-once or hide.
pub fn enter_overlay_mode_inner(
    app: &AppHandle,
    show_focused_once: bool,
) -> Result<(), String> {
    let win = app
        .get_webview_window("main")
        .ok_or_else(|| "main_window_missing".to_string())?;
    apply_overlay_chrome(&win)?;
    attach_overlay_dismiss(app, &win)?;
    crate::reregister_hotkey_best_effort(app);
    if show_focused_once {
        suppress_overlay_dismiss_briefly(app);
        win.show().map_err(|e| format!("show:{e}"))?;
        win.set_focus().map_err(|e| format!("set_focus:{e}"))?;
        let state = app.state::<AppState>();
        let mut g = state.inner.lock().map_err(|_| "state poisoned".to_string())?;
        g.pending_summon_hint = true;
    } else {
        win.hide().map_err(|e| format!("hide:{e}"))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overlay_chrome_descriptor_matches_search_config() {
        assert_eq!(
            overlay_chrome_descriptor(),
            (false, true, true, false),
            "must match clients/lumogis-search/src-tauri/tauri.conf.json main window"
        );
    }

    #[test]
    fn should_attach_dismiss_idempotent() {
        assert!(should_attach_dismiss(false));
        assert!(!should_attach_dismiss(true));
    }
}
