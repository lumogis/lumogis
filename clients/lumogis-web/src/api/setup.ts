// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-981 — first-admin setup. Two public routes (no credential):
//   GET  /api/v1/setup/status → { needs_setup: boolean }
//   POST /api/v1/setup/claim  → LoginResponse + the refresh cookie
// Never answer "needs setup" from /api/v1/auth/me (Lumogis Search reads that route).

import type { LoginResponse } from "./auth";

/** `unreachable` is "Core did not answer properly" (still starting): never the sign-in form. */
export type SetupStatus = "needed" | "not_needed" | "unreachable";

/** A hung Core must not leave the boot splash up forever; the caller re-asks. */
export const SETUP_STATUS_TIMEOUT_MS = 5000;

export async function getSetupStatus(): Promise<SetupStatus> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SETUP_STATUS_TIMEOUT_MS);
  try {
    const res = await fetch("/api/v1/setup/status", {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    // An older Core has no such route: there is nothing to set up.
    if (res.status === 404) return "not_needed";
    if (!res.ok) return "unreachable";
    const body = (await res.json()) as { needs_setup?: unknown };
    if (typeof body.needs_setup !== "boolean") return "unreachable";
    return body.needs_setup ? "needed" : "not_needed";
  } catch {
    return "unreachable"; // network error, timeout, or a non-JSON body
  } finally {
    clearTimeout(timer);
  }
}

export class SetupClaimError extends Error {
  readonly status: number;
  /** The string `detail`, or null (a Pydantic array detail, or no body). */
  readonly detail: string | null;
  /** The parsed JSON body, or null. */
  readonly body: unknown;

  constructor(status: number, detail: string | null, body: unknown) {
    super(detail ?? `setup claim failed (HTTP ${status})`);
    this.name = "SetupClaimError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

export async function claimSetup(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch("/api/v1/setup/claim", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* no JSON body */
    }
    const d = (body as { detail?: unknown } | null)?.detail;
    throw new SetupClaimError(res.status, typeof d === "string" ? d : null, body);
  }
  return (await res.json()) as LoginResponse;
}
