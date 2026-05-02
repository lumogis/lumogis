// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// In-memory access-token store. Per parent plan §Security decisions:
// the access token NEVER touches localStorage / sessionStorage / IndexedDB
// (XSS exfiltration surface). It lives only in this module's closure for
// the lifetime of the tab. The HttpOnly `lumogis_refresh` cookie is the
// durable credential and is restored on mount via `apiClient.tryRefresh()`.

export type AccessTokenListener = (token: string | null) => void;

export class AccessTokenStore {
  private token: string | null = null;
  private listeners: Set<AccessTokenListener> = new Set();

  get(): string | null {
    return this.token;
  }

  set(token: string | null): void {
    if (token === this.token) return;
    this.token = token;
    for (const fn of this.listeners) fn(token);
  }

  clear(): void {
    this.set(null);
  }

  subscribe(fn: AccessTokenListener): () => void {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }
}
