// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

//! OS system tray — shared AGPL seam (LUM-457).

use crate::{show_overlay_window, toggle_overlay_window};
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};

pub const TRAY_MENU_SHOW_ID: &str = "tray_show";
pub const TRAY_MENU_QUIT_ID: &str = "tray_quit";
pub const TRAY_MENU_SHOW_LABEL: &str = "Show Lumogis";
pub const TRAY_MENU_QUIT_LABEL: &str = "Quit";
pub const TRAY_TOOLTIP: &str = "Lumogis";

#[derive(Debug, Clone)]
pub struct TrayMenuItem {
    pub id: String,
    pub label: String,
}

#[derive(Debug, Clone)]
pub enum TrayMenuSpec {
    Hub,
    Custom { items: Vec<TrayMenuItem> },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayClickBehavior {
    ToggleOverlay,
    ShowMenu,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayQuitMode {
    ExitApp,
    ShutdownSupervisorThenExit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloseRequestedMode {
    ExitApp,
    HideWindow,
}

/// Host policy — two fields only (Thomas lock).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TrayConfig {
    pub quit_mode: TrayQuitMode,
    pub close_requested: CloseRequestedMode,
}

impl TrayConfig {
    pub fn search_default() -> Self {
        Self {
            quit_mode: TrayQuitMode::ExitApp,
            close_requested: CloseRequestedMode::ExitApp,
        }
    }

    pub fn hub_default() -> Self {
        Self {
            quit_mode: TrayQuitMode::ShutdownSupervisorThenExit,
            close_requested: CloseRequestedMode::HideWindow,
        }
    }

    pub fn server_default() -> Self {
        Self {
            quit_mode: TrayQuitMode::ShutdownSupervisorThenExit,
            close_requested: CloseRequestedMode::HideWindow,
        }
    }
}

/// Hub registers **before** `shared_setup` (private supervisor quit — no `bundled::` import in AGPL).
#[derive(Clone, Copy)]
pub struct TrayHostHooks {
    pub on_supervisor_quit: fn(&AppHandle),
    pub on_open_admin: Option<fn(&AppHandle)>,
    pub on_open_web: Option<fn(&AppHandle)>,
}

impl TrayHostHooks {
    pub fn hub_only(on_supervisor_quit: fn(&AppHandle)) -> Self {
        Self {
            on_supervisor_quit,
            on_open_admin: None,
            on_open_web: None,
        }
    }
}

fn resolve_tray_icon(app: &AppHandle) -> Option<Image<'static>> {
    if let Some(icon) = app.default_window_icon() {
        return Some(icon.clone().to_owned());
    }
    Image::from_bytes(include_bytes!("../icons/32x32.png")).ok()
}

fn dispatch_custom_menu_event(app: &AppHandle, item_id: &str, quit_mode: TrayQuitMode) {
    if item_id == TRAY_MENU_QUIT_ID {
        handle_tray_quit(app, quit_mode);
        return;
    }
    if item_id == TRAY_MENU_SHOW_ID {
        show_overlay_window(app);
        return;
    }
    let Some(hooks) = app.try_state::<TrayHostHooks>() else {
        tracing::error!("tray menu: TrayHostHooks missing for custom item {item_id}");
        return;
    };
    if let Some(handler) = hooks.on_open_admin {
        if item_id == "tray_open_admin" {
            handler(app);
            return;
        }
    }
    if let Some(handler) = hooks.on_open_web {
        if item_id == "tray_open_web" {
            handler(app);
            return;
        }
    }
    tracing::warn!("tray menu: unhandled custom item id {item_id}");
}

/// Install tray icon + menu. Non-fatal on failure.
pub fn install_system_tray(
    app: &AppHandle,
    config: TrayConfig,
    menu: TrayMenuSpec,
    click: TrayClickBehavior,
    tooltip: &str,
) {
    let Some(icon) = resolve_tray_icon(app) else {
        tracing::error!("system tray: no icon available (default_window_icon and include_bytes fallback failed)");
        return;
    };

    let quit_mode = config.quit_mode;
    let menu_items: Vec<MenuItem<tauri::Wry>> = match &menu {
        TrayMenuSpec::Hub => {
            let show_item = match MenuItem::with_id(
                app,
                TRAY_MENU_SHOW_ID,
                TRAY_MENU_SHOW_LABEL,
                true,
                None::<&str>,
            ) {
                Ok(item) => item,
                Err(e) => {
                    tracing::error!("system tray: menu show item failed: {e}");
                    return;
                }
            };
            let quit_item = match MenuItem::with_id(
                app,
                TRAY_MENU_QUIT_ID,
                TRAY_MENU_QUIT_LABEL,
                true,
                None::<&str>,
            ) {
                Ok(item) => item,
                Err(e) => {
                    tracing::error!("system tray: menu quit item failed: {e}");
                    return;
                }
            };
            vec![show_item, quit_item]
        }
        TrayMenuSpec::Custom { items } => {
            let mut built = Vec::with_capacity(items.len() + 1);
            for item in items {
                match MenuItem::with_id(app, &item.id, &item.label, true, None::<&str>) {
                    Ok(menu_item) => built.push(menu_item),
                    Err(e) => {
                        tracing::error!("system tray: custom menu item failed: {e}");
                        return;
                    }
                }
            }
            match MenuItem::with_id(
                app,
                TRAY_MENU_QUIT_ID,
                TRAY_MENU_QUIT_LABEL,
                true,
                None::<&str>,
            ) {
                Ok(quit_item) => built.push(quit_item),
                Err(e) => {
                    tracing::error!("system tray: menu quit item failed: {e}");
                    return;
                }
            }
            built
        }
    };

    let refs: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> = menu_items
        .iter()
        .map(|item| item as &dyn tauri::menu::IsMenuItem<tauri::Wry>)
        .collect();

    let tray_menu = match Menu::with_items(app, &refs) {
        Ok(menu) => menu,
        Err(e) => {
            tracing::error!("system tray: menu build failed: {e}");
            return;
        }
    };

    let click_behavior = click;
    let menu_spec = match &menu {
        TrayMenuSpec::Hub => TrayMenuSpec::Hub,
        TrayMenuSpec::Custom { items } => TrayMenuSpec::Custom {
            items: items.clone(),
        },
    };

    let mut builder = TrayIconBuilder::with_id("lumogis-tray")
        .icon(icon)
        .tooltip(tooltip)
        .menu(&tray_menu);

    builder = match click_behavior {
        TrayClickBehavior::ToggleOverlay => builder.show_menu_on_left_click(false),
        TrayClickBehavior::ShowMenu => builder.show_menu_on_left_click(true),
    };

    if let Err(e) = builder
        .on_menu_event(move |app, event| {
            let id = event.id().as_ref();
            match &menu_spec {
                TrayMenuSpec::Hub => match id {
                    TRAY_MENU_SHOW_ID => show_overlay_window(app),
                    TRAY_MENU_QUIT_ID => handle_tray_quit(app, quit_mode),
                    _ => {}
                },
                TrayMenuSpec::Custom { .. } => dispatch_custom_menu_event(app, id, quit_mode),
            }
        })
        .on_tray_icon_event(move |tray, event| {
            if click_behavior != TrayClickBehavior::ToggleOverlay {
                return;
            }
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                toggle_overlay_window(tray.app_handle());
            }
        })
        .build(app)
    {
        tracing::error!("system tray: TrayIconBuilder failed: {e}");
    }
}

fn handle_tray_quit(app: &AppHandle, quit_mode: TrayQuitMode) {
    match quit_mode {
        TrayQuitMode::ExitApp => app.exit(0),
        TrayQuitMode::ShutdownSupervisorThenExit => {
            match app.try_state::<TrayHostHooks>() {
                Some(hooks) => (hooks.on_supervisor_quit)(app),
                None => tracing::error!(
                    "tray Quit: TrayHostHooks missing for ShutdownSupervisorThenExit"
                ),
            }
        }
    }
}

/// Wire window close policy (Search exit vs Hub hide).
pub fn attach_close_requested_policy(app: &AppHandle, config: TrayConfig) {
    let app = app.clone();
    let mode = config.close_requested;
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    win.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            match mode {
                CloseRequestedMode::HideWindow => {
                    api.prevent_close();
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.hide();
                    }
                }
                CloseRequestedMode::ExitApp => {}
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_menu_constants() {
        assert_eq!(TRAY_MENU_SHOW_LABEL, "Show Lumogis");
        assert_eq!(TRAY_MENU_SHOW_ID, "tray_show");
        assert_eq!(TRAY_MENU_QUIT_ID, "tray_quit");
    }

    #[test]
    fn tray_config_defaults() {
        let search = TrayConfig::search_default();
        assert_eq!(search.quit_mode, TrayQuitMode::ExitApp);
        assert_eq!(search.close_requested, CloseRequestedMode::ExitApp);
        let hub = TrayConfig::hub_default();
        assert_eq!(hub.quit_mode, TrayQuitMode::ShutdownSupervisorThenExit);
        assert_eq!(hub.close_requested, CloseRequestedMode::HideWindow);
        let server = TrayConfig::server_default();
        assert_eq!(server.quit_mode, TrayQuitMode::ShutdownSupervisorThenExit);
        assert_eq!(server.close_requested, CloseRequestedMode::HideWindow);
    }

    #[test]
    fn shared_setup_accepts_tray_config() {
        let _ = TrayConfig::search_default();
        let _ = TrayConfig::hub_default();
        let _ = TrayConfig::server_default();
    }

    #[test]
    fn custom_menu_spec_accepts_host_items() {
        let spec = TrayMenuSpec::Custom {
            items: vec![
                TrayMenuItem {
                    id: "tray_open_admin".into(),
                    label: "Open settings".into(),
                },
                TrayMenuItem {
                    id: "tray_open_web".into(),
                    label: "Open Web".into(),
                },
            ],
        };
        match spec {
            TrayMenuSpec::Custom { items } => assert_eq!(items.len(), 2),
            _ => panic!("expected custom"),
        }
    }
}
