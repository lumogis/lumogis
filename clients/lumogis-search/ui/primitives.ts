// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

/** Line icon set (ported from docs/private/design/shared/primitives.jsx). */
const ICON_PATHS: Record<string, string> = {
  settings:
    '<circle cx="12" cy="12" r="3.2"/><path d="M12 3.5v2.2M12 18.3v2.2M4.6 7.8l1.9 1.1M17.5 15.1l1.9 1.1M4.6 16.2l1.9-1.1M17.5 8.9l1.9-1.1"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.1 5.1l1.8 1.8M17.1 17.1l1.8 1.8M5.1 18.9l1.8-1.8M17.1 6.9l1.8-1.8"/>',
  moon: '<path d="M20 13.5A8 8 0 1 1 10.5 4 6.3 6.3 0 0 0 20 13.5z"/>',
  doc: '<path d="M7 3.5h7L18 7.5V20.5H6.5V3.5z"/><path d="M13.5 3.5V8h4"/>',
  folder:
    '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l1.6 2H19a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
  alert: '<path d="M12 4 3 19h18z"/><path d="M12 10v4.5M12 17h.01"/>',
  refresh:
    '<path d="M5 9a7 7 0 0 1 12-3l2 2M19 15a7 7 0 0 1-12 3l-2-2"/><path d="M19 4v4h-4M5 20v-4h4"/>',
};

let logoMarkSeq = 0;

export function iconMarkup(name: string, size = 20): string {
  const paths = ICON_PATHS[name];
  if (!paths) {
    return "";
  }
  return `<svg class="lg-icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

/** Logo wrapped in the window drag handle (frameless overlay). */
export function logoDragHandleMarkup(size = 22): string {
  return `<div class="overlay-drag-region" data-tauri-drag-region title="Drag to move">${logoMarkMarkup(size)}</div>`;
}

export function logoMarkMarkup(size = 22): string {
  const id = `lm${++logoMarkSeq}${size}`;
  return `<svg class="overlay-logo" width="${size}" height="${size}" viewBox="0 0 64 64" fill="none" aria-hidden="true">
    <defs>
      <radialGradient id="c${id}" cx="40%" cy="35%" r="65%">
        <stop offset="0%" stop-color="#FFCC77"/>
        <stop offset="100%" stop-color="#E8890A"/>
      </radialGradient>
      <radialGradient id="s${id}" cx="40%" cy="35%" r="65%">
        <stop offset="0%" stop-color="#FFE0A0"/>
        <stop offset="100%" stop-color="#F5B845"/>
      </radialGradient>
    </defs>
    <g transform="translate(32 32) scale(0.66) translate(-50 -50)">
      <line x1="50" y1="50" x2="22" y2="24" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" opacity="0.5"/>
      <line x1="50" y1="50" x2="78" y2="28" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" opacity="0.5"/>
      <line x1="50" y1="50" x2="24" y2="72" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" opacity="0.5"/>
      <line x1="50" y1="50" x2="76" y2="74" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" opacity="0.5"/>
      <circle cx="22" cy="24" r="10" fill="url(#s${id})"/>
      <circle cx="78" cy="28" r="8" fill="url(#s${id})"/>
      <circle cx="24" cy="72" r="11" fill="url(#s${id})"/>
      <circle cx="76" cy="74" r="9" fill="url(#s${id})"/>
      <circle cx="50" cy="50" r="20" fill="url(#c${id})"/>
    </g>
  </svg>`;
}

export function scopePillMarkup(scope: string): string {
  const normalized =
    scope === "personal" || scope === "shared" || scope === "system" ? scope : "personal";
  const label =
    normalized === "personal"
      ? "Personal"
      : normalized === "shared"
        ? "Household"
        : "System";
  return `<span class="pill pill--${normalized}">${label}</span>`;
}

export function statusPillMarkup(label: string, tone: "ok" | "warn" = "ok"): string {
  return `<span class="overlay-status overlay-status--${tone}" role="status">
    <span class="overlay-status__dot" aria-hidden="true"></span>
    <span class="lg-mono overlay-status__label">${label}</span>
  </span>`;
}
