// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Browser Web Push helpers (Phase 4C). No logging of subscription material.

/**
 * Whether this environment can use the Push API with a service worker.
 * Requires a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts).
 */
export function isWebPushSupported(): boolean {
  if (typeof window === "undefined") return false;
  if (!window.isSecureContext) return false;
  if (!("serviceWorker" in navigator)) return false;
  if (!("PushManager" in window)) return false;
  return typeof Notification !== "undefined";
}

/** Decode VAPID public key from base64 URL (RFC 4648 §5) to bytes for {@link PushManager.subscribe}. */
export function urlBase64ToUint8Array(base64Url: string): Uint8Array {
  const pad = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = globalThis.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    out[i] = raw.charCodeAt(i);
  }
  return out;
}

/** Returns any existing Push subscription without prompting for permission (read-only probe). */
export async function getExistingBrowserPushSubscription(): Promise<PushSubscription | null> {
  if (!("serviceWorker" in navigator)) return null;
  const reg =
    (await navigator.serviceWorker.getRegistration()) ??
    ((await navigator.serviceWorker.getRegistration("/")) ?? null);
  if (!reg) return null;
  return reg.pushManager.getSubscription();
}

/**
 * Subscribes using VAPID `applicationServerKey`. Waits for `navigator.serviceWorker.ready`.
 * Does **not** call `Notification.requestPermission` — invoke that on explicit user gesture first.
 */
export async function createBrowserPushSubscription(vapidPublicKeyB64Url: string): Promise<PushSubscription> {
  const registration = await navigator.serviceWorker.ready;
  const key = urlBase64ToUint8Array(vapidPublicKeyB64Url);
  try {
    return await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: key,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "subscribe failed";
    throw new PushSubscribeError(msg);
  }
}

/** Browser-side subscribe failure after permission was granted (e.g. no worker, aborted). */
export class PushSubscribeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PushSubscribeError";
  }
}
