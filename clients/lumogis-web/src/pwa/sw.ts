// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// @ts-nocheck — Service worker context: standard lib `ServiceWorkerGlobalScope` / `PushEvent`
// typings are incomplete in the web client `tsconfig`; runtime behaviour matches the platform.
//
// Lumogis Web Phase 3B — Workbox injectManifest service worker.
// Precaches hashed static build artefacts only. No runtime route caches; no API interception.
// Phase 4D — `push` + `notificationclick` only (no fetches, no Cache Storage for API).
//

import {clientsClaim, setCacheNameDetails} from "workbox-core";
import {cleanupOutdatedCaches, precacheAndRoute} from "workbox-precaching";

import {normalizePushPayloadFromJson, sanitizeNotificationClickUrl} from "./swPush";

declare const __LUMOGIS_WEB_PKG_VERSION__: string | undefined;

const SW_ICON = "/icons/icon-192.png";

setCacheNameDetails({
  prefix: "lumogis-web",
  suffix: typeof __LUMOGIS_WEB_PKG_VERSION__ === "string" ? __LUMOGIS_WEB_PKG_VERSION__ : "dev",
  precache: "precache-v2",
  runtime: "runtime",
  googleAnalytics: "ga",
});

// `self.__WB_MANIFEST` is replaced by Workbox at build time — keep the identifier exact for injectManifest.
// @ts-expect-error — DOM `self` typing does not include `__WB_MANIFEST`; bundles as `ServiceWorkerGlobalScope`.
precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

void (self as unknown as {skipWaiting: () => Promise<void>}).skipWaiting();
clientsClaim();

//
// Phase 4D — visible notifications + safe in-app navigation (same-origin paths only).
//

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      let parsed: unknown;
      try {
        parsed = event.data ? await event.data.json() : undefined;
      } catch {
        parsed = undefined;
      }
      const payload = normalizePushPayloadFromJson(parsed);
      await self.registration.showNotification(payload.title, {
        body: payload.body,
        tag: payload.tag,
        data: {url: payload.targetPath},
        icon: SW_ICON,
        badge: SW_ICON,
      });
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  const notification = event.notification;
  notification.close();

  const rawUrl =
    notification.data &&
    typeof notification.data === "object" &&
    notification.data !== null &&
    "url" in notification.data
      ? (notification.data as {url?: unknown}).url
      : undefined;

  const safePath = sanitizeNotificationClickUrl(
    typeof rawUrl === "string" ? rawUrl : undefined,
  );
  const origin = self.location.origin;
  const safeUrl = new URL(safePath, origin).href;

  event.waitUntil(
    (async () => {
      const clientList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of clientList) {
        if (new URL(client.url).origin !== origin) continue;
        await client.focus();
        if ("navigate" in client && typeof client.navigate === "function") {
          await client.navigate(safeUrl);
        }
        return;
      }
      await self.clients.openWindow(safeUrl);
    })(),
  );
});
