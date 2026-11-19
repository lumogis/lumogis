// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-331 — the pure copy for Admin → Secure address.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  SECURE_ADDRESS_ERROR_CODES,
  SECURE_ADDRESS_STATES,
  type SecureAddressStatus,
} from "../../../src/api/secureAddress";
import {
  DETECTION_TEXT,
  ERROR_TEXT,
  NAME_NEW_CHECK_FAILED,
  NAME_NEW_TEXT,
  SECURE_ADDRESS_DOCS_URL,
  SUMMARY_TEXT,
  WARNING_TEXT,
  formatRetryAfter,
  isNameNew,
  secureAddressCopy,
} from "../../../src/features/admin/secureAddressCopy";

function status(over: Partial<SecureAddressStatus> = {}): SecureAddressStatus {
  return {
    state: "off",
    name: null,
    url: null,
    lan_address: null,
    lan_address_source: null,
    suggested_address: null,
    detection_unavailable_reason: null,
    certificate_valid_until: null,
    resolver: "unknown",
    error_code: null,
    retry_after_seconds: null,
    name_changed: false,
    name_new_until: null,
    sign_in_enabled: true,
    warnings: [],
    ...over,
  };
}

describe("secureAddressCopy", () => {
  it("every state and error code has words", () => {
    for (const st of SECURE_ADDRESS_STATES) {
      expect(secureAddressCopy(status({ state: st, error_code: "worker_unreachable" })).headline).not.toBe("");
      expect(SUMMARY_TEXT[st]).toBeTruthy();
    }
    for (const code of SECURE_ADDRESS_ERROR_CODES) {
      expect(ERROR_TEXT[code].length).toBeGreaterThan(20);
      expect(secureAddressCopy(status({ state: "error", error_code: code })).headline).toBe(
        code === "state_corrupt" ? ERROR_TEXT.state_corrupt : ERROR_TEXT[code],
      );
    }
    expect(Object.keys(WARNING_TEXT)).toHaveLength(4);
  });

  it("off: explains what becomes public and offers the enable form, no Turn off", () => {
    const c = secureAddressCopy(status());
    expect(c.form).toBe("enable");
    expect(c.showTurnOff).toBe(false);
    expect(c.details.join(" ")).toMatch(/public certificate logs/);
    expect(c.poll).toBe(false);
  });

  it("needs_address: uses the detection reason", () => {
    const c = secureAddressCopy(
      status({ state: "needs_address", detection_unavailable_reason: "docker_desktop" }),
    );
    expect(c.form).toBe("set_address");
    expect(c.details[0]).toMatch(/Docker Desktop/);
    expect(c.showTurnOff).toBe(true);
  });

  it("registering and issuing poll; ready and errors do not", () => {
    expect(secureAddressCopy(status({ state: "registering" })).poll).toBe(true);
    expect(secureAddressCopy(status({ state: "issuing" })).poll).toBe(true);
    expect(secureAddressCopy(status({ state: "ready" })).poll).toBe(false);
    expect(secureAddressCopy(status({ state: "error", error_code: "clock_skew" })).poll).toBe(false);
  });

  it("an error code on a working state is an extra line, not the headline", () => {
    const c = secureAddressCopy(
      status({ state: "registering", error_code: "worker_unreachable", retry_after_seconds: 300 }),
    );
    expect(c.headline).toMatch(/Getting a name/);
    expect(c.details).toEqual([ERROR_TEXT.worker_unreachable, "Lumogis tries again in about 5 minutes."]);
  });

  it("ready shows the address; router_blocked shows it and the router guide", () => {
    expect(secureAddressCopy(status({ state: "ready" }))).toMatchObject({
      showAddress: true,
      showRouterGuide: false,
      tone: "ok",
    });
    expect(secureAddressCopy(status({ state: "router_blocked" }))).toMatchObject({
      showAddress: true,
      showRouterGuide: true,
      tone: "warn",
    });
  });

  it("errors: Retry except where it cannot help; logs hint for certificate problems", () => {
    const c = (code: (typeof SECURE_ADDRESS_ERROR_CODES)[number]) =>
      secureAddressCopy(status({ state: "error", error_code: code }));
    expect(c("clock_skew").showRetry).toBe(true);
    expect(c("caddy_snippet_rejected").showRetry).toBe(true);
    expect(c("lan_address_unknown")).toMatchObject({ showRetry: false, form: "set_address" });
    expect(c("state_corrupt")).toMatchObject({ showRetry: false, form: "enable", showTurnOff: false });
    expect(c("certificate_pending").showLogsHint).toBe(true);
    expect(c("renewal_failing").showLogsHint).toBe(true);
    expect(c("worker_unreachable").showLogsHint).toBe(false);
  });

  it("formatRetryAfter rounds to a human unit", () => {
    expect(formatRetryAfter(30)).toBe("in about a minute");
    expect(formatRetryAfter(600)).toBe("in about 10 minutes");
    expect(formatRetryAfter(3 * 3600)).toBe("in about 3 hours");
    expect(formatRetryAfter(3 * 86400)).toBe("in about 3 days");
  });
});

describe("secureAddressCopy detection text (LUM-996)", () => {
  it("off with the docker_desktop reason and no suggestion: details include the how-to", () => {
    const c = secureAddressCopy(status({ detection_unavailable_reason: "docker_desktop" }));
    const text = c.details.join(" ");
    expect(c.details).toContain(DETECTION_TEXT.docker_desktop);
    expect(text).toContain("ipconfig getifaddr en0");
    expect(text).toContain("DHCP reservation"); // LUM-1004: Docker Desktop cannot follow a change
    expect(text).toContain("`ipconfig`");
  });

  it("off with a suggestion and a reason: no detection text", () => {
    const c = secureAddressCopy(
      status({ suggested_address: "192.168.1.20", detection_unavailable_reason: "docker_desktop" }),
    );
    expect(c.details).toEqual([expect.stringMatching(/secure address gives/), expect.stringMatching(/public DNS/)]);
    expect(c.details).not.toContain(DETECTION_TEXT.docker_desktop);
  });

  it("off with no suggestion and no reason: no detection text", () => {
    expect(secureAddressCopy(status()).details).toHaveLength(2);
  });

  it("needs_address with a suggestion: no detection text", () => {
    const c = secureAddressCopy(
      status({
        state: "needs_address",
        suggested_address: "192.168.1.30",
        detection_unavailable_reason: "failed",
      }),
    );
    expect(c.details).toEqual([]);
  });

  it("needs_address with no suggestion and a null reason: DETECTION_TEXT.failed", () => {
    const c = secureAddressCopy(status({ state: "needs_address" }));
    expect(c.details).toEqual([DETECTION_TEXT.failed]);
  });
});

describe("secureAddressCopy caddy_snippet_rejected (LUM-996)", () => {
  it("names Retry first and says to change the .env setting back", () => {
    const text = ERROR_TEXT.caddy_snippet_rejected;
    expect(text).toMatch(/Press Retry/);
    expect(text.indexOf("Retry")).toBeLessThan(text.indexOf("turn the secure address off and on"));
    expect(text).toMatch(/\.env file, change it back and run docker compose up -d\./);
    expect(secureAddressCopy(status({ state: "error", error_code: "caddy_snippet_rejected" })).headline).toBe(text);
  });
});

describe("the name-new window (LUM-996)", () => {
  const WINDOW = "2026-11-18T12:00:00Z";

  it("ready with name_new_until set: NAME_NEW_TEXT comes first and the router guide stays closed", () => {
    const c = secureAddressCopy(
      status({
        state: "ready",
        name_new_until: WINDOW,
        error_code: "renewal_failing",
      }),
    );
    expect(c.details[0]).toBe(NAME_NEW_TEXT);
    expect(c.details).toContain(ERROR_TEXT.renewal_failing);
    expect(c.showRouterGuide).toBe(false);
  });

  it("ready with name_new_until null: no NAME_NEW_TEXT", () => {
    const c = secureAddressCopy(status({ state: "ready", name_new_until: null }));
    expect(c.details).not.toContain(NAME_NEW_TEXT);
  });

  it("isNameNew is true for any non-null value, even one in the browser's past", () => {
    expect(isNameNew(status({ name_new_until: "2999-01-01T00:00:00Z" }))).toBe(true);
    expect(isNameNew(status({ name_new_until: "2000-01-01T00:00:00Z" }))).toBe(true);
    expect(isNameNew(status({ name_new_until: null }))).toBe(false);
  });

  it("NAME_NEW_TEXT and NAME_NEW_CHECK_FAILED give the same 30-minute figure", () => {
    const figure = (t: string) => t.match(/(\d+) minutes/)?.[1];
    expect(figure(NAME_NEW_TEXT)).toBe("30");
    expect(figure(NAME_NEW_CHECK_FAILED)).toBe(figure(NAME_NEW_TEXT));
  });
});

describe("SECURE_ADDRESS_DOCS_URL", () => {
  it("points at a heading that exists in remote-access.md", () => {
    expect(SECURE_ADDRESS_DOCS_URL).toMatch(/docs\/deployment\/remote-access\.md#secure-address-at-home$/);
    const doc = readFileSync(resolve(__dirname, "../../../../../docs/deployment/remote-access.md"), "utf8");
    expect(doc).toMatch(/^## Secure address at home$/m);
  });
});
