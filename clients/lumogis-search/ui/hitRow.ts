// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { invoke } from "@tauri-apps/api/core";
import { clampSnippet, type MemorySearchHit } from "./searchClient";

export type TauriInvoke = typeof invoke;

function escapeHtml(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** Build a search result row; open/reveal call `open_if_allowed` / `reveal_if_allowed` via Tauri. */
export function createHitRow(
  hit: MemorySearchHit,
  libraryRoots: string[],
  invokeFn: TauriInvoke = invoke,
): HTMLDivElement {
  const row = document.createElement("div");
  const disabled = !hit.id || hit.id.length === 0;
  row.className = `row${disabled ? " disabled" : ""}`;
  const title = hit.title?.trim() || hit.id || "Untitled";
  row.innerHTML = `
    <span class="score">${hit.score.toFixed(2)}</span>
    <div class="title">${escapeHtml(title)}</div>
    <div class="meta">${escapeHtml(hit.scope)}${hit.source ? ` · ${escapeHtml(hit.source)}` : ""}</div>
    <div class="snippet">${escapeHtml(clampSnippet(hit.snippet))}</div>
  `;
  if (!disabled) {
    row.addEventListener("click", async (ev) => {
      try {
        if (libraryRoots.length === 0) {
          alert("Set library roots in Settings to open this file locally.");
          return;
        }
        if (ev.shiftKey) {
          await invokeFn("reveal_if_allowed", { path: hit.id });
        } else {
          await invokeFn("open_if_allowed", { path: hit.id });
        }
      } catch (e) {
        alert(String(e));
      }
    });
    row.title = hit.id || title;
  } else {
    row.title = hit.owner_user_id
      ? `${hit.id || "Path unavailable"} (${hit.owner_user_id})`
      : hit.id || "Path unavailable";
  }
  return row;
}
