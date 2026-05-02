// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Shared Playwright helpers for deterministic RC gates (no credentials).

import { expect, type Page } from "@playwright/test";

export interface RcMonitor {
  readonly pageErrors: string[];
  readonly networkFailures: string[];
}

/** Track uncaught page errors and same-origin HTTP 5xx responses (excluding intentional probes). */
export function attachRcMonitoring(page: Page): RcMonitor {
  const pageErrors: string[] = [];
  const networkFailures: string[] = [];

  page.on("pageerror", (err) => {
    pageErrors.push(err.message);
  });

  page.on("response", (response) => {
    const status = response.status();
    if (status < 500) return;
    let respUrl: URL;
    try {
      respUrl = new URL(response.url());
    } catch {
      return;
    }
    let tabUrl: URL;
    try {
      tabUrl = new URL(page.url());
    } catch {
      return;
    }
    if (respUrl.origin !== tabUrl.origin) return;
    // Cookie refresh races many parallel workers against a single Core instance and can surface
    // transient 503s without indicating a UI regression; gate still catches real 5xx on app APIs.
    if (respUrl.pathname === "/api/v1/auth/refresh") return;
    // Web Push VAPID key endpoint intentionally returns 503 when keys are not configured (RC stack).
    if (respUrl.pathname === "/api/v1/notifications/vapid-public-key") return;
    networkFailures.push(`${status} ${response.url()}`);
  });

  return { pageErrors, networkFailures };
}

export function assertRcHealthy(m: RcMonitor): void {
  expect(m.pageErrors, m.pageErrors.join("\n")).toEqual([]);
  expect(m.networkFailures, m.networkFailures.join("\n")).toEqual([]);
}
