// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// One capture summary row (LUM-606). Status-agnostic so LUM-607's archive can
// reuse it for indexed captures.

import type { CaptureListItem } from "../../api/captures";

function statusColour(status: string): string {
  switch (status) {
    case "failed":
      return "#c62828";
    case "indexed":
      return "#2e7d32";
    default:
      return "#ed6c02"; // pending
  }
}

function preview(item: CaptureListItem): string {
  const t = (item.title ?? "").trim();
  if (t) return t;
  const body = (item.text ?? "").trim();
  if (body) return body.length > 120 ? `${body.slice(0, 120)}…` : body;
  if (item.url) return item.url;
  return "(no text)";
}

export function CaptureCard({
  item,
  onOpen,
}: {
  item: CaptureListItem;
  onOpen: (id: string) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={() => onOpen(item.id)}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        border: "1px solid #e0e0e0",
        borderRadius: 8,
        padding: "0.75rem 1rem",
        marginBottom: "0.5rem",
        background: "transparent",
        cursor: "pointer",
        minHeight: "44px", // mobile tap target
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
        <span style={{ fontSize: "0.95rem" }}>{preview(item)}</span>
        <span
          style={{ color: statusColour(item.status), fontWeight: 600, fontSize: "0.8rem" }}
        >
          {item.status}
        </span>
      </div>
      <div style={{ fontSize: "0.8rem", opacity: 0.7, marginTop: "0.25rem" }}>
        Updated {new Date(item.updated_at).toLocaleString()}
        {item.attachment_count ? ` · ${item.attachment_count} attachment(s)` : ""}
      </div>
      {item.status === "failed" && item.last_error ? (
        <div style={{ fontSize: "0.8rem", color: "#c62828", marginTop: "0.25rem" }}>
          Add-to-memory failed: {item.last_error}
        </div>
      ) : null}
    </button>
  );
}
