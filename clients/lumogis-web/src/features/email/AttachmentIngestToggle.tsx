// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-702 — the per-mailbox opt-in. LUM-727 changed what it MEANS.
//
// It used to say "file every attachment from this mailbox automatically". It now
// says "show me this mailbox's attachments so I can decide". The column kept its
// name (`attachment_ingest_enabled`) because the change is safe in that
// direction only — strictly less happens without the user, so an existing `true`
// cannot cause a surprise filing. The COPY is not safe to leave alone: every
// string below used to promise automatic filing, and a control that promises
// what it no longer does is worse than one that was never there.
//
// Two things this control refuses to do.
//
// It never enables itself. Surfacing someone's mail for review without an
// explicit consent step is the behaviour this product exists to be the opposite
// of, so the default is off and stays off until someone acts.
//
// It never lets a metadata-mode mailbox reach the 422. Such a mailbox never
// fetched message bodies, so there are no attachments to review and enabling
// would be a silent no-op. The server refuses it too, but a disabled control
// with an explanation is the version the user can act on — a rejected request
// after the click is worse news, later.

import { useCallback, useId, useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { ApiClient } from "../../api/client";
import {
  canFileAttachments,
  METADATA_MODE_EXPLANATION,
  setAttachmentIngest,
} from "../../api/emailAttachmentIngest";

export interface AttachmentIngestToggleProps {
  client: ApiClient;
  sourceId: string;
  /** The mailbox's `extraction_method`. Only `imap_full_ingest` can file. */
  extractionMethod: string;
  enabled: boolean;
  onChange?: (enabled: boolean) => void;
}

export function AttachmentIngestToggle({
  client,
  sourceId,
  extractionMethod,
  enabled,
  onChange,
}: AttachmentIngestToggleProps): JSX.Element {
  const supported = canFileAttachments(extractionMethod);
  const [applyToExisting, setApplyToExisting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const switchLabelId = useId();

  const onToggle = useCallback(
    (next: boolean) => {
      setBusy(true);
      setAttachmentIngest(client, sourceId, {
        enabled: next,
        apply_to_existing: next ? applyToExisting : false,
      })
        .then(() => {
          onChange?.(next);
          setStatus(
            next
              ? applyToExisting
                ? "Attachments from this mailbox, including existing mail, are ready to review"
                : "New attachments from this mailbox will appear for review"
              : "Stopped showing this mailbox's attachments",
          );
        })
        .catch(() => setStatus("Could not update this setting — try again"))
        .finally(() => setBusy(false));
    },
    [applyToExisting, client, onChange, sourceId],
  );

  return (
    <div className="grid gap-3">
      <div className="flex items-start gap-3 text-sm">
        <Switch
          checked={enabled}
          disabled={!supported || busy}
          onCheckedChange={(on: boolean) => onToggle(on)}
          aria-labelledby={switchLabelId}
        />
        <span id={switchLabelId} className="font-medium">
          Review this mailbox&rsquo;s attachments for my document library
        </span>
      </div>
      {/* Says the quiet part out loud. Without it the switch still reads like
          the old "file everything", and a user who expects that will conclude
          the feature is broken when their library stays empty. */}
      <p data-testid="ingest-explanation" className="m-0 text-sm text-muted-foreground">
        Nothing is filed automatically. Attachments appear in a list for you to
        pick from, and you can set up rules to file the recurring ones.
      </p>

      {!supported ? (
        <p data-testid="ingest-unsupported" className="m-0 text-sm text-muted-foreground">
          {METADATA_MODE_EXPLANATION}
        </p>
      ) : (
        <Label className="items-start">
          <Checkbox
            checked={applyToExisting}
            disabled={enabled || busy}
            onCheckedChange={(on: boolean) => setApplyToExisting(on)}
            aria-label="Also include attachments from existing mail"
          />
          Also include attachments from existing mail
        </Label>
      )}

      {status ? (
        <p role="status" data-testid="ingest-status" className="m-0 text-sm text-muted-foreground">
          {status}
        </p>
      ) : null}
    </div>
  );
}
