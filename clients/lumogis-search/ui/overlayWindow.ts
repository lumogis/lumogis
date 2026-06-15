// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { getCurrentWindow } from "@tauri-apps/api/window";

let logoDragBound = false;

/**
 * Logo-only window drag for the frameless overlay.
 * Uses `data-tauri-drag-region` plus `startDragging` as a Linux fallback.
 */
export function wireLogoDrag(root: HTMLElement): void {
  if (logoDragBound) {
    return;
  }
  logoDragBound = true;
  root.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) {
      return;
    }
    const handle = (ev.target as HTMLElement | null)?.closest<HTMLElement>(
      ".overlay-drag-region",
    );
    if (!handle) {
      return;
    }
    void getCurrentWindow().startDragging();
  });
}

/** Test-only reset for Vitest isolation. */
export function resetLogoDragStateForTests(): void {
  logoDragBound = false;
}

export function focusSearchInput(root: HTMLElement, settingsOpen: boolean): void {
  if (settingsOpen) {
    return;
  }
  const q = root.querySelector<HTMLInputElement>("#q");
  if (!q || q.disabled) {
    return;
  }
  q.focus();
}
