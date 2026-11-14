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
import { starvationWarning, type StarvationWarning } from "./externalConsumerWarning";

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
    <section aria-labelledby="organize-heading">
      <h3 id="organize-heading">Organize my mailbox</h3>
      <p>
        Let Lumogis apply what it has learned to your real mailbox. This is off by
        default and always reversible.
      </p>

      <label>
        <input
          type="checkbox"
          checked={labelOn}
          onChange={(e) => {
            clearWarning();
            setLabelOn(e.target.checked);
          }}
        />{" "}
        Label my emails so they can be sorted
      </label>
      {labelOn && !labelVisible && (
        <p role="note" data-testid="label-caveat">
          Heads up: your provider may not <em>show</em> these labels in its own app.
          To see the organization visibly, turn on “Sort into folders” below.
        </p>
      )}

      <label>
        <input
          type="checkbox"
          checked={folderOn}
          onChange={(e) => {
            clearWarning();
            setFolderOn(e.target.checked);
          }}
        />{" "}
        Sort my emails into folders (moves mail)
      </label>
      {folderOn && (
        <fieldset data-testid="folder-categories">
          <legend>Which categories to sort into folders</legend>
          {sortable.map((c) => (
            <label key={c.slug}>
              <input
                type="checkbox"
                checked={folderCats.includes(c.slug)}
                onChange={() => toggleCat(c.slug)}
              />{" "}
              {c.label}
            </label>
          ))}
          <p>“Other” is never moved — unsorted mail stays in your inbox.</p>
        </fieldset>
      )}

      <label>
        <input
          type="checkbox"
          checked={applyExisting}
          onChange={(e) => {
            clearWarning();
            setApplyExisting(e.target.checked);
          }}
        />{" "}
        Also apply to existing mail (not just new). Duplicates are skipped.
      </label>

      {starvation && (
        <div role="alertdialog" aria-labelledby="starve-heading" data-testid="starvation-warning">
          <h4 id="starve-heading">This will hide mail from paperless</h4>
          <p>{starvation.message}</p>
          {/* Offered to EVERY provider. What matters for paperless is only that
              the message stays in the inbox, which labels-only guarantees
              everywhere — label and move are separate operations server-side. */}
          <p data-testid="starvation-alternative">{starvation.alternative}</p>
          {!labelVisible && (
            <p data-testid="starvation-label-caveat">
              On your provider you will not <em>see</em> the category on the message
              itself, but your documents keep arriving in paperless.
            </p>
          )}
          {/* Undo is a real recovery path and must not be oversold. */}
          <p data-testid="starvation-recovery">{starvation.recovery}</p>
          <button type="button" onClick={useLabelsInstead}>
            Use labels instead
          </button>
          <button
            type="button"
            data-testid="starvation-continue"
            onClick={() => saveM.mutate(true)}
            disabled={saveM.isPending}
          >
            I understand — sort into folders anyway
          </button>
          <button type="button" onClick={clearWarning}>
            Cancel
          </button>
        </div>
      )}

      <div>
        <button type="button" onClick={() => saveM.mutate(false)} disabled={saveM.isPending}>
          Save
        </button>
        {/* LUM-729: the scope was hardcoded to "all", so the folder-only put-back
            — the recovery path for exactly the user this feature warns — was
            unreachable from the UI. A warning that implies recoverability beside
            a recovery path nobody can reach is a promise the product does not
            keep. */}
        <label>
          What to undo{" "}
          <select
            aria-label="What to undo"
            value={undoScope}
            onChange={(e) => setUndoScope(e.target.value as UndoScope)}
          >
            <option value="all">Labels and folder sorting</option>
            <option value="folder">Folder sorting only (move mail back to the inbox)</option>
            <option value="label">Labels only</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => undoM.mutate(undoScope)}
          disabled={undoM.isPending}
        >
          Undo
        </button>
        <button type="button" onClick={() => void logQ.refetch()}>
          Show activity
        </button>
      </div>

      {msg && (
        <p role="status" data-testid="organize-save-msg">
          {msg}
        </p>
      )}

      {/* LUM-729 follow-up: the undo sweep is bounded now, and a bound that gave
          up invisibly would be the same silent failure the warning above exists
          to stop. Shown with the activity log because that is where the
          un-reversed messages are listed. */}
      {logQ.data?.undo_gave_up_at && (
        <p role="alert" data-testid="undo-gave-up">
          Lumogis stopped trying to undo {logQ.data.undo_unreversed ?? 0} message
          {(logQ.data.undo_unreversed ?? 0) === 1 ? "" : "s"} — they could not be moved
          back, most likely because they were deleted or moved in your mail app.
          They stay where Lumogis put them. Organizing has resumed.
        </p>
      )}

      {logQ.data && (
        <ul data-testid="organize-log">
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
