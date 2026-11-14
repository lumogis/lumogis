// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * Test helper (LUM-886): stub `navigator.serviceWorker` with a push subscription and two
 * shown notifications. Call `removeServiceWorker()` in `afterEach` (the descriptor is
 * defined on the live `navigator`, so a leftover stub leaks into later tests).
 */
import { vi } from "vitest";

export interface PushStub {
  sub: PushSubscription;
  unsubscribe: ReturnType<typeof vi.fn>;
  notifications: Array<{ close: ReturnType<typeof vi.fn> }>;
}

export function installServiceWorker(
  opts: { withSubscription?: boolean; getRegistration?: () => Promise<unknown> } = {},
): PushStub {
  const unsubscribe = vi.fn(async () => true);
  const sub = {
    toJSON: () => ({ endpoint: "https://push.example/e1", keys: { p256dh: "pk", auth: "ak" } }),
    unsubscribe,
  } as unknown as PushSubscription;
  const notifications = [{ close: vi.fn() }, { close: vi.fn() }];
  const registration = {
    pushManager: { getSubscription: async () => (opts.withSubscription === false ? null : sub) },
    getNotifications: async () => notifications,
  };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { getRegistration: opts.getRegistration ?? (async () => registration) },
  });
  return { sub, unsubscribe, notifications };
}

export function removeServiceWorker(): void {
  delete (navigator as unknown as Record<string, unknown>).serviceWorker;
}
