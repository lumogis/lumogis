// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

export type AuthMode = "unknown" | "off" | "on" | "unreachable";

export type OverlaySettingsLike = {
  onboardingComplete?: boolean;
  libraryRoots: string[];
};

export function needsOnboarding(settings: OverlaySettingsLike): boolean {
  return !settings.onboardingComplete;
}

export function isSearchDisabled(needsLogin: boolean, authMode: AuthMode): boolean {
  return needsLogin || authMode === "unreachable";
}

export function canManageIngestPaths(authMode: AuthMode, sessionRole: string | null): boolean {
  return authMode === "off" || sessionRole === "admin";
}

export function canUploadIngest(authMode: AuthMode, sessionPresent: boolean): boolean {
  return authMode === "off" || sessionPresent;
}

export function onboardingContinueEnabled(
  healthStatus: string,
  authMode: AuthMode,
  sessionPresent: boolean,
): boolean {
  if (healthStatus !== "ok") return false;
  if (authMode === "unreachable") return false;
  if (authMode === "on" && !sessionPresent) return false;
  return true;
}

export function onboardingMarkup(params: {
  wizardBaseUrl: string;
  healthStatus: string;
  healthMessage: string;
  authMode: AuthMode;
  sessionPresent: boolean;
  loginError: string;
}): string {
  const { wizardBaseUrl, healthStatus, healthMessage, authMode, sessionPresent, loginError } =
    params;
  const showAuthLogin = authMode === "on" && healthStatus === "ok";
  const continueDisabled = !onboardingContinueEnabled(healthStatus, authMode, sessionPresent);
  return `
    <div class="onboarding-panel" id="onboarding">
      <div class="settings-brand">
        <span class="settings-wordmark">Lumogis Search</span>
      </div>
      <h1 class="onboarding-title">Connect to your household Lumogis</h1>
      <p class="hint">Enter the URL of your Lumogis server (HTTPS recommended on the internet; <code>http://</code> is fine on your home network).</p>
      <label>Server URL
        <input type="url" id="onboard-base" value="${escapeHtml(wizardBaseUrl)}" placeholder="https://lumogis.example.com" />
      </label>
      <div class="toolbar">
        <button type="button" class="primary" id="btn-test-connection">Test connection</button>
      </div>
      ${
        healthStatus === "unreachable" || healthStatus === "degraded"
          ? `<div class="banner err" id="onboard-health">${escapeHtml(healthMessage || "Cannot reach server — check URL and network.")}</div>`
          : healthStatus === "ok"
            ? `<div class="banner ok" id="onboard-health">Server is reachable.</div>`
            : ""
      }
      ${
        showAuthLogin
          ? `<div class="login-panel" id="onboard-login">
        <p class="hint">Sign in with your household account.</p>
        <label>Email <input id="onboard-email" type="email" autocomplete="username" /></label>
        <label>Password <input id="onboard-password" type="password" minlength="12" autocomplete="current-password" /></label>
        <div class="toolbar">
          <button type="button" class="primary" id="btn-onboard-login">Sign in</button>
        </div>
        <p class="hint err-text" id="onboard-login-error">${escapeHtml(loginError)}</p>
      </div>`
          : authMode === "off" && healthStatus === "ok"
            ? `<p class="hint">Auth is off on this server — no sign-in required.</p>`
            : ""
      }
      <div class="toolbar onboarding-actions">
        <button type="button" class="primary" id="btn-onboard-continue" ${continueDisabled ? "disabled" : ""}>Continue</button>
      </div>
    </div>`;
}

function escapeHtml(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
