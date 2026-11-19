// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-837 chunk B — inline edit of a source's name, category and poll interval.
//
// These three are the general fields `PATCH /sources/{id}` gained in chunk A;
// before that the route was an IMAP-only mode toggle and no source of any type
// could be renamed or re-scheduled.
//
// `url` is absent by design, not omission. Changing it is indistinguishable from
// deleting the source and creating another — the poll cursor, the detected
// `source_type` and the extraction method all derive from the original, and
// `signals.source_id` has no FK, so history would silently re-attribute. For
// paperless it would also be close to decorative: the poller reads the
// *credential's* `base_url`, not `sources.url`.
import { useState } from "react";

import type { SourcePatch, SourceRow } from "../../api/sources";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";

export function EditSourceRow({
  row,
  busy,
  onSave,
  onCancel,
}: {
  row: SourceRow;
  busy: boolean;
  onSave: (patch: SourcePatch) => void;
  onCancel: () => void;
}): JSX.Element {
  const [name, setName] = useState(row.name);
  const [category, setCategory] = useState(row.category);
  const [interval, setInterval] = useState(String(row.poll_interval));

  // Send every field the form owns, not just the changed ones. The route
  // computes `status: "unchanged"` itself by comparing against the stored row,
  // so a client-side diff here would duplicate that logic in a second place
  // — and the two would drift.
  const submit = () =>
    onSave({
      name,
      category,
      poll_interval: Number(interval),
    });

  return (
    <li className="py-3">
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor={`source-name-${row.id}`}>Name</Label>
          <Input
            id={`source-name-${row.id}`}
            aria-label={`Name for ${row.name}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`source-category-${row.id}`}>Category</Label>
          <Input
            id={`source-category-${row.id}`}
            aria-label={`Category for ${row.name}`}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`source-interval-${row.id}`}>Check every (seconds, at least 60)</Label>
          <Input
            id={`source-interval-${row.id}`}
            aria-label={`Poll interval for ${row.name}`}
            type="number"
            min={60}
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
          />
        </div>
        <div className="flex gap-2 sm:col-span-2">
          <Button type="submit" disabled={busy}>
            Save {row.name}
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        </div>
      </form>
    </li>
  );
}
