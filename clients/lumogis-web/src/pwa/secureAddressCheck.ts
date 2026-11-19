// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-331 — "can this device open the secure address?" A `no-cors` fetch of the
// address's health endpoint: an opaque response means the name resolved and TLS
// held (the browser validated the certificate); a TypeError or timeout means it
// did not. The page's CSP allows https://*.homes.lumogis.app in connect-src for
// exactly this.
//
// It only runs when the page itself was opened by a home network address or
// localhost. From a public, tunnel or Tailscale origin, Chrome's local-network
// access permission would make a working router look blocked, so callers say
// "check from a device on your home Wi-Fi" instead.

const PRIVATE_IPV4 = /^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})$/;

/** True for a hostname the device check may run from: localhost or an RFC1918 IPv4. */
export function isHomeNetworkHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  if (h === "localhost" || h === "127.0.0.1" || h === "[::1]") return true;
  return PRIVATE_IPV4.test(h);
}

/** True when the page is already served over https (the secure address or another https name). */
export function isSecurePageOrigin(loc: Pick<Location, "protocol"> = window.location): boolean {
  return loc.protocol === "https:";
}

export function canCheckFromHere(loc: Pick<Location, "hostname"> = window.location): boolean {
  return isHomeNetworkHost(loc.hostname);
}

/**
 * Resolves true when `{url}/api/v1/health` answered (opaque), false on any
 * network error or after `timeoutMs`. Never throws.
 */
export async function checkReachable(
  url: string,
  timeoutMs = 4000,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const target = `${url.replace(/\/+$/, "")}/api/v1/health`;
    await fetchImpl(target, {
      mode: "no-cors",
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}
