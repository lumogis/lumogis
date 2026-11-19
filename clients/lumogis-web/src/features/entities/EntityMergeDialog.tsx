// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "It's the same as…" dialog (LUM-965) — merge another of the member's own entries
// into the one whose page they are on. The entry whose page it is survives.
//
// Step 1 picks the other entry (search over the member's own personal entries, or
// `presetOther` from the "name in use" offer, which skips it). Step 2 says what will
// happen *before* anything is sent: which name stays, a kind line when the kinds differ,
// and what becomes visible to the household when exactly one side is shared. The body is
// exactly `{ other_id, version, other_version, keep_name }`; ownership, sharing rules and
// the undo record are all decided on the server.
/* eslint-disable react-refresh/only-export-components */

import { useEffect, useId, useState, type ReactNode } from "react";

import type { ApiClient } from "../../api/client";
import {
  getEntity,
  kgSearch,
  mergeEntity,
  parseEntityEditError,
  type EntityCard,
  type EntityEditError,
  type EntityMergeResponse,
} from "../../api/search";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { kindNoun, withArticle } from "./EntityKindDialog";

export interface EntityMergeDialogProps {
  /** The surviving entry (this page's); needs `version`. */
  entry: EntityCard;
  client: ApiClient;
  /** Skips the picker: the entry that will be merged into `entry` (needs `version`). */
  presetOther?: EntityCard;
  onClose: () => void;
  /** Called with the server's answer after a successful merge. */
  onMerged: (res: EntityMergeResponse) => void;
  /** "Reload" after a version conflict, or when the other entry is gone (a merge may have landed). */
  onReload?: () => void;
}

const kindOf = (c: EntityCard): string => (c.type || "CONCEPT").toUpperCase();

/**
 * Entries the member can merge: their own personal entries, not this one.
 *
 * Staged entries are not filtered here: `GET /api/v1/kg/search` returns them and
 * its card carries no `is_staged`. Picking one ends in the `staged_entry` message
 * below (the server refuses it). Filtering needs `is_staged` on the search card or a
 * staged filter on the search route (LUM-965 verify, considered, not filed).
 */
export function mergeCandidates(results: EntityCard[], thisId: string): EntityCard[] {
  return results.filter(
    (c) => c.is_owner === true && c.scope === "personal" && c.entity_id !== thisId,
  );
}

/** The sharing consequence (or null when neither or both sides are shared). */
export function sharingLine(keep: EntityCard, other: EntityCard): string | null {
  const keepShared = keep.share_status === "shared";
  const otherShared = other.share_status === "shared";
  if (keepShared && !otherShared) {
    return `“${other.name}” will be visible to your household as another name for “${keep.name}”.`;
  }
  if (!keepShared && otherShared) {
    return `“${keep.name}” will now be shared with your household.`;
  }
  return null;
}

export function EntityMergeDialog({
  entry,
  client,
  presetOther,
  onClose,
  onMerged,
  onReload,
}: EntityMergeDialogProps): JSX.Element {
  const baseId = useId();
  const [other, setOther] = useState<EntityCard | null>(presetOther ?? null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EntityCard[]>([]);
  const [searching, setSearching] = useState(false);
  const [keepName, setKeepName] = useState<"this" | "other">("this");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<EntityEditError | "generic" | null>(null);

  useEffect(() => {
    if (other !== null) return;
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const ctrl = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(() => {
      kgSearch(client, q, 20, ctrl.signal)
        .then((res) => {
          if (!ctrl.signal.aborted) setResults(mergeCandidates(res.entities, entry.entity_id));
        })
        .catch(() => {
          if (!ctrl.signal.aborted) setResults([]);
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setSearching(false);
        });
    }, 200);
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [client, entry.entity_id, other, query]);

  // A search hit carries no `version`; the merge needs both, so load the full card.
  const pick = async (hit: EntityCard): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      setOther(await getEntity(client, hit.entity_id));
    } catch {
      setError("generic");
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = async (): Promise<void> => {
    if (busy || other === null) return;
    setBusy(true);
    setError(null);
    try {
      const res = await mergeEntity(client, entry.entity_id, {
        other_id: other.entity_id,
        version: entry.version ?? 1,
        other_version: other.version ?? 1,
        keep_name: keepName,
      });
      onMerged(res);
    } catch (err) {
      const parsed = parseEntityEditError(err) ?? "generic";
      setError(parsed);
      // A 404 for the other entry usually means the merge already landed and only the
      // response was lost: refresh the page and its History so the member sees the truth.
      if (parsed !== "generic" && parsed.code === "entity_not_found" && parsed.which === "other") {
        onReload?.();
      }
    } finally {
      setBusy(false);
    }
  };

  const sharing = other ? sharingLine(entry, other) : null;
  const kindsDiffer = other !== null && kindOf(other) !== kindOf(entry);

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent data-testid="entity-merge-dialog">
        <div className="flex flex-col gap-3">
          <DialogHeader>
            <DialogTitle>It&apos;s the same as…</DialogTitle>
          </DialogHeader>

          {other === null ? (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${baseId}-search`}>Find the other entry</Label>
                <Input
                  id={`${baseId}-search`}
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Type a name"
                  autoFocus
                />
              </div>
              {searching && <p className="m-0 text-xs text-muted-foreground">Searching…</p>}
              {!searching && query.trim().length >= 2 && results.length === 0 && (
                <p className="m-0 text-xs text-muted-foreground">No other entries match.</p>
              )}
              <ul role="list" className="m-0 flex list-none flex-col gap-1 p-0">
                {results.map((r) => (
                  <li key={r.entity_id}>
                    <Button
                      type="button"
                      variant="ghost"
                      className="w-full justify-start"
                      disabled={busy}
                      onClick={() => void pick(r)}
                    >
                      <span>{r.name}</span>{" "}
                      <span className="text-muted-foreground">{kindNoun(r.type)}</span>
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <>
              <p className="m-0">
                Merge “{other.name}” into “{entry.name}”? One entry will remain.
              </p>
              <RadioGroup
                aria-label="Name"
                value={keepName}
                onValueChange={(v) => setKeepName(v as "this" | "other")}
              >
                <div className="flex items-center gap-2">
                  <RadioGroupItem id={`${baseId}-keep-this`} value="this" />
                  <Label htmlFor={`${baseId}-keep-this`} className="font-normal">
                    Keep “{entry.name}”
                  </Label>
                </div>
                <div className="flex items-center gap-2">
                  <RadioGroupItem id={`${baseId}-keep-other`} value="other" />
                  <Label htmlFor={`${baseId}-keep-other`} className="font-normal">
                    Keep “{other.name}”
                  </Label>
                </div>
              </RadioGroup>
              <p className="m-0 text-xs text-muted-foreground">
                The other name is kept as another name, so Lumogis still recognises it.
              </p>
              {kindsDiffer && (
                <p className="m-0 text-xs text-muted-foreground" data-testid="merge-kind-line">
                  “{other.name}” is {withArticle(kindNoun(other.type))}; the entry stays{" "}
                  {withArticle(kindNoun(entry.type))}, and Lumogis will remember that “{other.name}” is{" "}
                  {withArticle(kindNoun(entry.type))}.
                </p>
              )}
              {sharing && (
                <p className="m-0 text-xs text-muted-foreground" data-testid="merge-sharing-line">
                  {sharing}
                </p>
              )}
              <p className="m-0 text-xs text-muted-foreground">
                You can undo this from the entry&apos;s History.
              </p>
            </>
          )}

          {error !== null && (
            <MergeErrorMessage
              error={error}
              onReload={onReload}
              thisName={entry.name}
              otherName={other?.name ?? null}
            />
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            {other !== null && (
              <Button type="button" onClick={() => void onSubmit()} disabled={busy}>
                {busy ? "Merging…" : "Merge"}
              </Button>
            )}
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function MergeErrorMessage({
  error,
  onReload,
  thisName,
  otherName,
}: {
  error: EntityEditError | "generic";
  onReload?: () => void;
  thisName: string;
  otherName: string | null;
}): JSX.Element {
  if (error === "generic") {
    return <MergeAlert>Couldn&apos;t merge. Try again.</MergeAlert>;
  }
  const which = "which" in error ? error.which : undefined;
  const named = which === "other" ? otherName : which === "this" ? thisName : null;
  const reload = onReload && (
    <Button type="button" variant="outline" size="sm" onClick={onReload}>
      Reload
    </Button>
  );
  switch (error.code) {
    case "version_conflict":
      return (
        <MergeAlert>
          {error.which === "other" ? "The other entry" : "This entry"} changed since you opened it.
          Reload and try again. {reload}
        </MergeAlert>
      );
    case "staged_entry":
      return (
        <MergeAlert>
          That entry is still being checked and can&apos;t be merged yet.
        </MergeAlert>
      );
    case "same_entry":
      return (
        <MergeAlert>
          That is this same entry. Pick a different one.
        </MergeAlert>
      );
    case "not_owner":
    case "not_editable":
    case "entity_not_found":
      return (
        <MergeAlert which={error.which}>
          {named === null ? "That entry" : `'${named}'`} can&apos;t be merged
          {error.code === "entity_not_found"
            ? ": it no longer exists (it may already have been merged)."
            : error.code === "not_editable"
              ? ": it is the household copy of one of your entries. Merge from your own entry instead."
              : ": only your own entries can be merged, not ones shared by someone else."}{" "}
          {reload}
        </MergeAlert>
      );
    default:
      return (
        <MergeAlert>
          Couldn&apos;t merge. Try again.
        </MergeAlert>
      );
  }
}

function MergeAlert({ children, which }: { children: ReactNode; which?: string }): JSX.Element {
  return (
    <Alert variant="destructive" data-which={which}>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}
