// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Hand-written types for the auth wire contract.
//
// We hand-write these instead of consuming the openapi-typescript generated
// shapes because:
//
// 1. The shapes are stable (verified against the shipped `routes/auth.py`
//    +  `models/auth.py` in parent plan §Codebase context line 95-96 and the
//    plan's R4 self-review wire-literal corrections).
// 2. AuthProvider is bootstrapped at module init and must compile without
//    requiring `pnpm codegen` to have run first.
//
// If a future shipped change drifts these shapes, the
// `test_lumogis_web_auth_alignment.py` server-side tests (parent plan §Test
// cases #1–5, #18, #19) will catch it before any client work breaks.

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: UserPublic;
}

export interface UserPublic {
  id: string;
  email: string;
  role: "admin" | "user";
}

// Wire-literal error details verified against shipped `routes/auth.py`
// (parent plan R4 self-review log + plan line 95).
export const AUTH_ERROR_LITERALS = {
  INVALID_CREDENTIALS: "invalid credentials",
  MISSING_REFRESH_COOKIE: "missing refresh cookie",
  INVALID_REFRESH_TOKEN: "invalid refresh token",
  REFRESH_ROTATION_FAILED: "refresh rotation failed",
  TOO_MANY_FAILED_ATTEMPTS: "too many failed attempts; try again in a minute",
  LOGIN_DISABLED_DEV_MODE: "login is disabled in single-user dev mode",
  REFRESH_DISABLED_DEV_MODE: "refresh is disabled in single-user dev mode",
} as const;
