// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-645 — per-mailbox opt-in organization settings: label + folder-sort, with
// the non-Gmail "labels may be invisible" steer, an apply-to-existing option, and
// a bulk undo. Both opt-ins default off and are reversible.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { ApiError } from "../../api/client";
import { getTaxonomy, type CategoryItem } from "../../api/meEmailLabels";
import {
  getEmailOrganizeLog,
  setEmailOrganize,
  undoEmailOrganize,
  type UndoScope,
} from "../../api/emailSources";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { starvationWarning, type StarvationWarning } from "./externalConsumerWarning";

const UNDO_SCOPES: { value: UndoScope; label: string }[] = [
  { value: "all", label: "Labels and folder sorting" },
  { value: "folder", label: "Folder sorting only (move mail back to the inbox)" },
  { value: "label", label: "Labels only" },
];

export interface EmailOrganizeSettingsProps {
  sourceId: string;
  /** From the provider registry: does this provider display Lumogis labels? */
  labelVisible: boolean;
}

export function EmailOrganizeSettings({
  sourceId,
  labelVisible,
}: EmailOrganizeSettingsProps): JSX.Element {
  const { client } = useAuth();
  const [labelOn, setLabelOn] = useState(false);
  const [folderOn, setFolderOn] = useState(false);
  const [folderCats, setFolderCats] = useState<string[]>([]);
  const [applyExisting, setApplyExisting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // LUM-729. Held as state rather than derived from the mutation error so that
  // changing any toggle clears it: an acknowledgement is of a specific
  // consequence, and carrying it across an edited request would acknowledge
  // something the user never saw.
  const [starvation, setStarvation] = useState<StarvationWarning | null>(null);
  const [undoScope, setUndoScope] = useState<UndoScope>("all");

  const taxQ = useQuery({
    queryKey: ["email-labels", "taxonomy"],
    queryFn: () => getTaxonomy(client),
  });
  // Folder-sort is per-category and never sorts the protected catch-all ('other').
  const sortable: CategoryItem[] = (taxQ.data?.categories ?? []).filter(
    (c) => c.enabled && !c.is_protected,
  );

  const onErr = (e: unknown) =>
    setMsg(e instanceof ApiError ? "Could not save — try again." : "Failed");

  const saveM = useMutation({
    // `acknowledged` is a parameter, not state read at call time: the first
    // attempt must go WITHOUT it, and only the explicit "continue anyway" press
    // sends it. A component that always sent `true` would turn the gate into a
    // dismissible banner in JSON, which is the shape the design rejects.
    mutationFn: (acknowledged: boolean = false) =>
      setEmailOrganize(client, sourceId, {
        organize_label: labelOn,
        organize_folder: folderOn,
        // Only send categories when folder-sort is on — otherwise the backend
        // validator 422s (categories require organize_folder=true).
        organize_folder_categories: folderOn ? folderCats : [],
        apply_to_existing: applyExisting,
        ...(acknowledged ? { acknowledge_external_consumer: true } : {}),
      }),
    onSuccess: (r) => {
      setStarvation(null);
      setMsg(
        r.apply_to_existing
          ? "Saved. Lumogis will organize new and existing mail over the next little while."
          : "Saved. Lumogis will organize new mail from now on.",
      );
    },
    onError: (e: unknown) => {
      const warn = starvationWarning(e);
      if (warn) {
        setStarvation(warn);
        setMsg(null);
        return;
      }
      onErr(e);
    },
  });

  const undoM = useMutation({
    mutationFn: (scope: UndoScope) => undoEmailOrganize(client, sourceId, scope),
    onSuccess: (r) =>
      setMsg(
        r.scope === "folder"
          ? "Undo started — Lumogis is moving mail back to your inbox."
          : r.scope === "label"
            ? "Undo started — Lumogis is removing its labels."
            : "Undo started — Lumogis is removing its labels and un-sorting mail.",
      ),
    onError: onErr,
  });

  /** Any edit invalidates a warning shown for a different request — an
   *  acknowledgement is of a specific consequence. */
  const clearWarning = () => setStarvation(null);

  /** The alternative the warning offers, applied to the form. Labels do not move
   *  mail, so paperless keeps receiving everything — true on EVERY provider,
   *  because the label and move operations are independent server-side.
   *
   *  It does not save for the user: they should see what they are about to
   *  agree to before agreeing to it. */
  const useLabelsInstead = () => {
    setStarvation(null);
    setFolderOn(false);
    setLabelOn(true);
    setFolderCats([]);
    setApplyExisting(false);
    setMsg("Switched to labels only. Press Save to apply.");
  };

  const logQ = useQuery({
    queryKey: ["email-organize-log", sourceId],
    queryFn: () => getEmailOrganizeLog(client, sourceId),
    enabled: false,
  });

  const toggleCat = (slug: string) => {
    clearWarning();
    setFolderCats((cur) =>
      cur.includes(slug) ? cur.filter((s) => s !== slug) : [...cur, slug],
    );
  };

  return (
    <section aria-labelledby="organize-heading" className="grid gap-4">
      <div>
        <h3 id="organize-heading" className="m-0 text-sm font-semibold">
          Organize my mailbox
        </h3>
        <p className="m-0 mt-1 text-sm text-muted-foreground">
          Let Lumogis apply what it has learned to your real mailbox. This is off by
          default and always reversible.
        </p>
      </div>

      <Label className="items-start">
        <Checkbox
          checked={labelOn}
          onCheckedChange={(on: boolean) => {
            clearWarning();
            setLabelOn(on);
          }}
        />
        Label my emails so they can be sorted
      </Label>
      {labelOn && !labelVisible && (
        <p role="note" data-testid="label-caveat" className="m-0 text-sm text-muted-foreground">
          Heads up: your provider may not <em>show</em> these labels in its own app.
          To see the organization visibly, turn on “Sort into folders” below.
        </p>
      )}

      <Label className="items-start">
        <Checkbox
          checked={folderOn}
          onCheckedChange={(on: boolean) => {
            clearWarning();
            setFolderOn(on);
          }}
        />
        Sort my emails into folders (moves mail)
      </Label>
      {folderOn && (
        <fieldset data-testid="folder-categories" className="m-0 grid gap-2 rounded-lg border p-3">
          <legend className="px-1 text-sm font-medium">Which categories to sort into folders</legend>
          {sortable.map((c) => (
            <Label key={c.slug} className="items-start">
              <Checkbox
                checked={folderCats.includes(c.slug)}
                onCheckedChange={() => toggleCat(c.slug)}
              />
              {c.label}
            </Label>
          ))}
          <p className="m-0 text-sm text-muted-foreground">
            “Other” is never moved — unsorted mail stays in your inbox.
          </p>
        </fieldset>
      )}

      <Label className="items-start">
        <Checkbox
          checked={applyExisting}
          onCheckedChange={(on: boolean) => {
            clearWarning();
            setApplyExisting(on);
          }}
        />
        Also apply to existing mail (not just new). Duplicates are skipped.
      </Label>

      {starvation && (
        <Alert data-testid="starvation-warning" aria-labelledby="starve-heading">
          <AlertTitle id="starve-heading">This will hide mail from paperless</AlertTitle>
          <AlertDescription className="grid gap-2">
            <p className="m-0">{starvation.message}</p>
            {/* Offered to EVERY provider. What matters for paperless is only that
                the message stays in the inbox, which labels-only guarantees
                everywhere — label and move are separate operations server-side. */}
            <p className="m-0" data-testid="starvation-alternative">
              {starvation.alternative}
            </p>
            {!labelVisible && (
              <p className="m-0" data-testid="starvation-label-caveat">
                On your provider you will not <em>see</em> the category on the message
                itself, but your documents keep arriving in paperless.
              </p>
            )}
            {/* Undo is a real recovery path and must not be oversold. */}
            <p className="m-0" data-testid="starvation-recovery">
              {starvation.recovery}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={useLabelsInstead}>
                Use labels instead
              </Button>
              <Button
                type="button"
                variant="outline"
                data-testid="starvation-continue"
                onClick={() => saveM.mutate(true)}
                disabled={saveM.isPending}
              >
                I understand — sort into folders anyway
              </Button>
              <Button type="button" variant="ghost" onClick={clearWarning}>
                Cancel
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <Button type="button" onClick={() => saveM.mutate(false)} disabled={saveM.isPending}>
          Save
        </Button>
      </div>

      {/* LUM-729: the scope was hardcoded to "all", so the folder-only put-back
          — the recovery path for exactly the user this feature warns — was
          unreachable from the UI. A warning that implies recoverability beside
          a recovery path nobody can reach is a promise the product does not
          keep. */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label id="undo-scope-label">What to undo</Label>
          <Select
            items={UNDO_SCOPES}
            value={undoScope}
            onValueChange={(v) => setUndoScope(v as UndoScope)}
          >
            <SelectTrigger aria-labelledby="undo-scope-label" className="w-full min-w-64 sm:w-auto">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {UNDO_SCOPES.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => undoM.mutate(undoScope)}
          disabled={undoM.isPending}
        >
          Undo
        </Button>
        <Button type="button" variant="ghost" onClick={() => void logQ.refetch()}>
          Show activity
        </Button>
      </div>

      {msg && (
        <p role="status" data-testid="organize-save-msg" className="m-0 text-sm text-muted-foreground">
          {msg}
        </p>
      )}

      {/* LUM-729 follow-up: the undo sweep is bounded now, and a bound that gave
          up invisibly would be the same silent failure the warning above exists
          to stop. Shown with the activity log because that is where the
          un-reversed messages are listed. */}
      {logQ.data?.undo_gave_up_at && (
        <Alert variant="destructive" data-testid="undo-gave-up">
          <AlertDescription>
            Lumogis stopped trying to undo {logQ.data.undo_unreversed ?? 0} message
            {(logQ.data.undo_unreversed ?? 0) === 1 ? "" : "s"} — they could not be moved
            back, most likely because they were deleted or moved in your mail app.
            They stay where Lumogis put them. Organizing has resumed.
          </AlertDescription>
        </Alert>
      )}

      {logQ.data && (
        <ul data-testid="organize-log" className="m-0 grid gap-1 pl-5 text-sm">
          {logQ.data.entries.map((e, i) => (
            <li key={i}>
              {e.op === "label" ? "Labelled" : "Sorted"} → {e.target}
              {e.reversed_at ? " (undone)" : ""}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
