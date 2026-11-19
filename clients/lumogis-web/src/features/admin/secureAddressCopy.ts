// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-331 — pure copy for Admin → Secure address: no React, no fetching. The
// server derives `state` and `error_code`; this module only turns them into
// words and says which controls the page shows (same pattern as
// knowledgeGraphCopy.ts).

import type {
  DetectionUnavailableReason,
  SecureAddressState,
  LanAddressSource,
  SecureAddressErrorCode,
  SecureAddressStatus,
  SecureAddressWarning,
} from "../../api/secureAddress";

/** Public handbook anchor; must match the remote-access heading "Secure address at home". */
export const SECURE_ADDRESS_DOCS_URL =
  "https://github.com/lumogis/lumogis/blob/main/docs/deployment/remote-access.md#secure-address-at-home";

export const CADDY_LOGS_COMMAND = "docker compose logs caddy";

/** The household's DNS answers can be cached for up to 30 minutes after a name is new. */
export const NAME_NEW_TEXT =
  "The name is new. Phones and laptops can take up to 30 minutes to find it, so it may not open on them yet.";

export const NAME_NEW_CHECK_FAILED =
  "This device cannot open the secure address yet. The name is new: devices can take up to 30 minutes to find it. Check again later.";

export const DETECTING_TEXT = "Looking for this computer's home network address…";

/** While the name is new, an open card refetches this often so the copy ends with the window. */
export const NAME_NEW_REFETCH_MS = 60_000;

/**
 * The server returns `null` once the name-new window has ended, so the browser's clock is
 * never consulted: a device with a wrong clock cannot show or hide the copy wrongly.
 */
export function isNameNew(s: SecureAddressStatus): boolean {
  return s.name_new_until !== null;
}

export type SecureAddressTone = "ok" | "info" | "warn";

export interface SecureAddressCopy {
  headline: string;
  tone: SecureAddressTone;
  details: string[];
  /** The address form: turn on (state off / state_corrupt) or set the address (needs_address). */
  form: "enable" | "set_address" | null;
  showRetry: boolean;
  showTurnOff: boolean;
  showRouterGuide: boolean;
  showLogsHint: boolean;
  /** The address is serving: show it, Copy, and Check on this device. */
  showAddress: boolean;
  /** Poll every 3 s while Lumogis is still working on it. */
  poll: boolean;
}

/** One sentence per error code. Typed as a full Record so a new server code fails the build. */
export const ERROR_TEXT: Record<SecureAddressErrorCode, string> = {
  worker_unreachable:
    "Lumogis cannot reach its name service right now. It keeps trying on its own.",
  registration_refused:
    "Lumogis's name service turned this Lumogis away. Update Lumogis, then press Retry.",
  registration_rate_limited:
    "Too many new secure addresses came from your network this week. Lumogis tries again later on its own.",
  clock_skew:
    "This computer's clock is wrong, so the name service cannot trust it. Set the correct time, then press Retry.",
  budget_exhausted:
    "Lumogis has given out all its new certificates for this week. Yours comes when next week's allowance starts; Lumogis keeps trying.",
  lan_address_unknown:
    "Lumogis could not find this computer's home network address. Type it below.",
  caddy_volume_missing:
    "The front door (Caddy) cannot see the secure-address settings. Update Lumogis to the latest version, then press Retry.",
  caddy_snippet_rejected:
    "The front door (Caddy) did not accept the secure-address settings. Press Retry. If this happens again, turn the secure address off and on. If you just changed a secure-address setting in the .env file, change it back and run docker compose up -d.",
  caddy_reload_failed:
    "The front door (Caddy) could not load the secure-address settings. Press Retry.",
  caddy_unreachable:
    "Lumogis cannot reach its front door (Caddy). Check that it is running with: docker compose ps",
  stack_control_outdated:
    "Part of Lumogis (stack-control) is older than the rest. Update Lumogis, then press Retry.",
  certificate_pending:
    "The certificate has not arrived after 15 minutes. The front door's log says why.",
  renewal_failing:
    "The certificate is about to run out and has not renewed. The front door's log says why.",
  state_corrupt:
    "The secure-address settings were damaged and have been reset. Turn it on again.",
  registration_retired:
    "This version of Lumogis can no longer use the name service. Update Lumogis.",
  caddy_image_outdated:
    "The front door (Caddy) is too old for the secure address. Update Lumogis to the latest version, then press Retry.",
  service_full: "Lumogis's name service is full right now. Try again later.",
};

export const WARNING_TEXT: Record<SecureAddressWarning, string> = {
  caddy_unreachable:
    "Lumogis has not been able to check the front door (Caddy) for a while. The address may still work.",
  address_mismatch:
    "This computer's home network address seems to have changed. Check the address below.",
  two_computers:
    "Two computers may be using this secure address. Only one Lumogis can have it: turn it off on the other one.",
  budget_exhausted: ERROR_TEXT.budget_exhausted,
};

export const DETECTION_TEXT: Record<DetectionUnavailableReason, string> = {
  docker_desktop:
    "Lumogis runs in Docker Desktop, which hides this computer's home network address. To find it on a Mac, run `ipconfig getifaddr en0` in Terminal (or open System Settings → Wi-Fi → Details). On Windows, run `ipconfig` and use the IPv4 address of the Wi-Fi or Ethernet adapter. Or open Lumogis at http://that-address instead of localhost, and Lumogis fills it in. Docker Desktop cannot notice when that address changes, so give this computer a fixed address in your router (a DHCP reservation); otherwise use Change here whenever it moves.",
  not_rfc1918:
    "Lumogis could not find a home network address for this computer. Type the address your phone or laptop uses to open Lumogis, for example 192.168.1.20.",
  failed:
    "Lumogis could not find this computer's home network address. Type the address your phone or laptop uses to open Lumogis, for example 192.168.1.20.",
  helper_image_missing:
    "Lumogis could not look up this computer's home network address (a helper image is missing; updating Lumogis fixes it). Type the address your phone or laptop uses to open Lumogis, for example 192.168.1.20.",
};

export const SOURCE_TEXT: Record<LanAddressSource, string> = {
  detected: "found automatically",
  request: "the address you opened this page with",
  manual: "typed by you",
};

/**
 * Codes where Retry cannot help: the admin acts in the form, or Lumogis waits out a
 * weekly limit. (`state_corrupt` returns earlier with the Turn on form.)
 */
const NO_RETRY_CODES: ReadonlySet<SecureAddressErrorCode> = new Set<SecureAddressErrorCode>([
  "budget_exhausted",
  "lan_address_unknown",
]);

const LOGS_CODES: ReadonlySet<SecureAddressErrorCode> = new Set<SecureAddressErrorCode>([
  "certificate_pending",
  "renewal_failing",
]);

export const WHAT_IT_DOES =
  "A secure address gives this Lumogis a name with a real certificate, so phones and laptops on your home Wi-Fi open it over https. Voice input, installing the app and notifications need that. It works only at home; using Lumogis away from home is a separate, paid service.";

export const WHAT_BECOMES_PUBLIC =
  "Turning it on tells Lumogis's name service this computer's home network address (for example 192.168.1.20). The name and that address become visible in public DNS and in public certificate logs. Nobody outside your home network can open the address.";

/** One-line state for the System status page. */
export const SUMMARY_TEXT: Record<SecureAddressState, string> = {
  off: "Off",
  needs_address: "Needs this computer's home network address",
  registering: "Getting a name",
  issuing: "Getting a certificate",
  ready: "On",
  router_blocked: "On, but your router hides it",
  error: "Needs attention",
};

export function formatRetryAfter(seconds: number): string {
  if (seconds < 90) return "in about a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `in about ${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `in about ${hours} hours`;
  return `in about ${Math.round(hours / 24)} days`;
}

export function secureAddressCopy(s: SecureAddressStatus): SecureAddressCopy {
  const base: SecureAddressCopy = {
    headline: "",
    tone: "info",
    details: [],
    form: null,
    showRetry: false,
    showTurnOff: true,
    showRouterGuide: false,
    showLogsHint: false,
    showAddress: false,
    poll: false,
  };
  const overlay: string[] = [];
  if (s.error_code !== null && s.state !== "error") overlay.push(ERROR_TEXT[s.error_code]);
  if (s.retry_after_seconds !== null && s.retry_after_seconds > 0) {
    overlay.push(`Lumogis tries again ${formatRetryAfter(s.retry_after_seconds)}.`);
  }

  switch (s.state) {
    case "off":
      return {
        ...base,
        headline: "The secure address is off.",
        details: [
          WHAT_IT_DOES,
          WHAT_BECOMES_PUBLIC,
          ...(s.suggested_address === null && s.detection_unavailable_reason !== null
            ? [DETECTION_TEXT[s.detection_unavailable_reason]]
            : []),
        ],
        form: "enable",
        showTurnOff: false,
      };
    case "needs_address":
      return {
        ...base,
        headline: "Lumogis needs this computer's home network address.",
        tone: "warn",
        details: [
          ...(s.suggested_address === null
            ? [
                s.detection_unavailable_reason !== null
                  ? DETECTION_TEXT[s.detection_unavailable_reason]
                  : DETECTION_TEXT.failed,
              ]
            : []),
          ...overlay,
        ],
        form: "set_address",
      };
    case "registering":
      return {
        ...base,
        headline: "Getting a name for this Lumogis…",
        details: overlay,
        poll: true,
      };
    case "issuing":
      return {
        ...base,
        headline: "Getting a certificate. This usually takes a minute or two.",
        details: overlay,
        poll: true,
      };
    case "ready":
      return {
        ...base,
        headline: "Your secure address is ready.",
        tone: "ok",
        details: [...(isNameNew(s) ? [NAME_NEW_TEXT] : []), ...overlay],
        showAddress: true,
      };
    case "router_blocked":
      return {
        ...base,
        headline: "Your secure address works, but your router hides it from your devices.",
        tone: "warn",
        details: [
          "Many routers block names that point into your home network (DNS rebind protection). Allow homes.lumogis.app in your router once, using the steps below.",
          ...overlay,
        ],
        showRouterGuide: true,
        showAddress: true,
      };
    case "error": {
      const code = s.error_code ?? "worker_unreachable";
      if (code === "state_corrupt") {
        return {
          ...base,
          headline: ERROR_TEXT.state_corrupt,
          tone: "warn",
          details: [WHAT_IT_DOES, WHAT_BECOMES_PUBLIC],
          form: "enable",
          showTurnOff: false,
        };
      }
      return {
        ...base,
        headline: ERROR_TEXT[code],
        tone: "warn",
        details: overlay,
        form: code === "lan_address_unknown" ? "set_address" : null,
        showRetry: !NO_RETRY_CODES.has(code),
        showLogsHint: LOGS_CODES.has(code),
      };
    }
  }
}
