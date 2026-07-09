// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

//! Wayland overlay re-summon / recovery decision logic (LUM-455).
//!
//! Pure, window-free helpers so the decisions unit-test without a Tauri app
//! handle. The side-effecting glue (`apply_summon`, recovery confirmation) lives
//! in `lib.rs` where the `AppHandle` is available.

/// A summon action parsed from forwarded argv (single-instance) or first-launch argv.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SummonAction {
    Toggle,
    Show,
    Hide,
}

/// Where a summon originated — determines whether it can *confirm* Wayland recovery.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SummonSource {
    /// CLI / single-instance forwarded invocation (a DE keybinding). Guaranteed-availability path.
    Cli,
    /// System-tray click or menu. Guaranteed-focus path where the tray is visible.
    Tray,
    /// In-app global hotkey — best-effort on Wayland; never confirms recovery.
    Hotkey,
}

/// Desktop environment, detected from `XDG_CURRENT_DESKTOP` (env passed in for tests).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DesktopEnv {
    Gnome,
    Kde,
    Other,
}

impl DesktopEnv {
    pub fn as_str(self) -> &'static str {
        match self {
            DesktopEnv::Gnome => "gnome",
            DesktopEnv::Kde => "kde",
            DesktopEnv::Other => "other",
        }
    }
}

/// Parse a summon action from argv. Recognises `--toggle`/`--show`/`--hide`;
/// precedence when several are present: `show` > `hide` > `toggle`. Unknown or
/// no recognised flag → `None` (normal launch).
pub fn parse_summon_args(argv: &[String]) -> Option<SummonAction> {
    let has = |flag: &str| argv.iter().any(|a| a == flag);
    if has("--show") {
        Some(SummonAction::Show)
    } else if has("--hide") {
        Some(SummonAction::Hide)
    } else if has("--toggle") {
        Some(SummonAction::Toggle)
    } else {
        None
    }
}

// Wayland detection is `overlay_window::is_wayland_session()` (WAYLAND_DISPLAY) —
// the single source of truth used by `apply_summon`, cold-start, and the Hub. We
// deliberately do NOT add a second `XDG_SESSION_TYPE`-based check here to avoid a
// diverging gate (WAYLAND_DISPLAY is set by all real Wayland sessions).

/// Detect the desktop environment from `XDG_CURRENT_DESKTOP` (may be colon-separated
/// and any case, e.g. `"ubuntu:GNOME"`).
pub fn detect_desktop_env(current_desktop: Option<&str>) -> DesktopEnv {
    let Some(cd) = current_desktop else {
        return DesktopEnv::Other;
    };
    let lower = cd.to_ascii_lowercase();
    if lower.split(':').any(|p| p == "gnome" || p == "unity") {
        DesktopEnv::Gnome
    } else if lower.split(':').any(|p| p == "kde" || p == "plasma") {
        DesktopEnv::Kde
    } else {
        DesktopEnv::Other
    }
}

/// Whether the overlay should show on cold start as the Wayland recovery safety net
/// (LUM-455). True only on Wayland, while recovery is unconfirmed and the user has not
/// opted out.
///
/// Deliberately **not** gated on `onboarding_complete`: a fresh Wayland user must be
/// able to *reach* onboarding, and the in-app hotkey cannot summon the window on
/// Wayland. `show_overlay_window` already adapts presentation per onboarding phase
/// (chrome only once onboarding is complete).
pub fn cold_start_should_show(
    wayland: bool,
    recovery_confirmed: bool,
    show_once_opt_out: bool,
) -> bool {
    wayland && !recovery_confirmed && !show_once_opt_out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn parse_summon_args_recognises_flags() {
        assert_eq!(parse_summon_args(&s(&["--toggle"])), Some(SummonAction::Toggle));
        assert_eq!(parse_summon_args(&s(&["--show"])), Some(SummonAction::Show));
        assert_eq!(parse_summon_args(&s(&["--hide"])), Some(SummonAction::Hide));
    }

    #[test]
    fn parse_summon_args_none_for_unknown_or_empty() {
        assert_eq!(parse_summon_args(&s(&[])), None);
        assert_eq!(parse_summon_args(&s(&["--frob", "x"])), None);
    }

    #[test]
    fn parse_summon_args_precedence_show_over_toggle() {
        assert_eq!(parse_summon_args(&s(&["--toggle", "--show"])), Some(SummonAction::Show));
        assert_eq!(parse_summon_args(&s(&["--hide", "--toggle"])), Some(SummonAction::Hide));
    }

    #[test]
    fn detect_desktop_env_handles_compound_and_case() {
        assert_eq!(detect_desktop_env(Some("ubuntu:GNOME")), DesktopEnv::Gnome);
        assert_eq!(detect_desktop_env(Some("KDE")), DesktopEnv::Kde);
        assert_eq!(detect_desktop_env(Some("plasma")), DesktopEnv::Kde);
        assert_eq!(detect_desktop_env(Some("sway")), DesktopEnv::Other);
        assert_eq!(detect_desktop_env(None), DesktopEnv::Other);
    }

    #[test]
    fn cold_start_only_on_unconfirmed_wayland() {
        assert!(cold_start_should_show(true, false, false));
        assert!(!cold_start_should_show(true, true, false)); // confirmed
        assert!(!cold_start_should_show(true, false, true)); // opted out
        assert!(!cold_start_should_show(false, false, false)); // X11
    }
}
