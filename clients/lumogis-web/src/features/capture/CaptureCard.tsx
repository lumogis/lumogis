// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// One capture summary row (LUM-606). Status-agnostic so LUM-607's archive can
// reuse it for indexed captures.

import type { CaptureListItem } from "../../api/captures";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { formatDateTime } from "../../util/formatTimestamp";

function statusLabel(status: string): string {
  switch (status) {
    case "failed":
      return "Failed";
    case "indexed":
      return "In memory";
    default:
      return "Waiting";
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
    <Button
      type="button"
      variant="outline"
      onClick={() => onOpen(item.id)}
      className="mb-2 block h-auto min-h-11 w-full whitespace-normal px-4 py-3 text-left"
    >
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0 text-sm break-words">{preview(item)}</span>
        <Badge variant={item.status === "failed" ? "destructive" : item.status === "indexed" ? "default" : "secondary"}>
          {statusLabel(item.status)}
        </Badge>
      </span>
      <span className="mt-1 block text-xs font-normal text-muted-foreground">
        Updated {formatDateTime(item.updated_at)}
        {item.attachment_count ? ` · ${item.attachment_count} attachment(s)` : ""}
      </span>
      {item.status === "failed" && item.last_error ? (
        <span className="mt-1 block text-xs font-normal text-destructive">
          Add-to-memory failed: {item.last_error}
        </span>
      ) : null}
    </Button>
  );
}
