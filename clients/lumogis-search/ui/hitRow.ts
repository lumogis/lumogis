// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Thomas Kohlborn, trading as Lumogis

import { invoke } from "@tauri-apps/api/core";
import { clampSnippet, type MemorySearchHit } from "./searchClient";
import { iconMarkup, scopePillMarkup } from "./primitives";

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
  selected = false,
): HTMLButtonElement {
  const row = document.createElement("button");
  const disabled = !hit.id || hit.id.length === 0;
  row.type = "button";
  row.className = `hit-row${disabled ? " hit-row--disabled" : ""}${selected ? " hit-row--selected" : ""}`;
  const title = hit.title?.trim() || hit.id || "Untitled";
  row.innerHTML = `
    <div class="hit-row__head">
      <span style="color:var(--accent-ink)">${iconMarkup("doc", 14)}</span>
      <span class="hit-row__title">${escapeHtml(title)}</span>
      ${scopePillMarkup(hit.scope)}
      ${hit.source ? `<span class="hit-row__source">${escapeHtml(hit.source)}</span>` : ""}
    </div>
    <span class="hit-row__snippet">${escapeHtml(clampSnippet(hit.snippet))}</span>
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
    row.disabled = true;
    row.title = hit.owner_user_id
      ? `${hit.id || "Path unavailable"} (${hit.owner_user_id})`
      : hit.id || "Path unavailable";
  }
  return row;
}
