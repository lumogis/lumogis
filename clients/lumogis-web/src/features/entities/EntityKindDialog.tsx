// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "It's not a person, it's a…" dialog (LUM-967) — change what kind an entry is.
// Owner-only; the caller (EntityCardPanel) decides when to offer it.
//
// The body is `{ entity_type, version }` and nothing else (the server accepts one
// change at a time). The dialog says up front that Lumogis will remember the
// correction, because the server records a per-member rule in the same
// transaction (a re-read of this name as the old kind is stored as the new one).
//
// When another of the member's entries of the target kind already uses the name (the
// server's `409 name_in_use`), the dialog offers "Merge with …" (LUM-965): the entry that
// already has the target kind survives, this one is merged into it, and the page moves on
// to the survivor (`onMergedAway`) because the entry it showed no longer exists.
/* eslint-disable react-refresh/only-export-components */

import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { ApiClient } from "../../api/client";
import {
  editEntity,
  getEntity,
  parseEntityEditError,
  type EntityCard,
  type EntityEditError,
  type EntityTypeValue,
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
import { Label } from "../../components/ui/label";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { EntityMergeDialog } from "./EntityMergeDialog";

export interface EntityKindDialogProps {
  card: EntityCard;
  client: ApiClient;
  onClose: () => void;
  /** Called with the updated card after a successful save. */
  onSaved: (card: EntityCard) => void;
  /** Called when the member chooses "Reload" after a version conflict. */
  onReload?: () => void;
  /**
   * Called with the survivor's id after a "Merge with …" from the name-in-use offer:
   * this entry no longer exists, so the caller moves to the survivor's page.
   */
  onMergedAway?: (survivorId: string) => void;
}

/** The kinds a member can pick (extraction has no "place"), with singular nouns. */
export const KIND_CHOICES: ReadonlyArray<{ value: EntityTypeValue; noun: string }> = [
  { value: "PERSON", noun: "person" },
  { value: "ORG", noun: "organisation" },
  { value: "PROJECT", noun: "project" },
  { value: "CONCEPT", noun: "concept" },
];

/** Singular noun for a stored kind ("org" → "organisation"; unknown → lower-cased; null → "entry"). */
export function kindNoun(type: string | null | undefined): string {
  if (!type) return "entry";
  const hit = KIND_CHOICES.find((k) => k.value === type.toUpperCase());
  return hit ? hit.noun : type.toLowerCase().replace(/_/g, " ");
}

/** "an organisation", "a person" (an before a vowel). */
export function withArticle(noun: string): string {
  return `${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}`;
}

export function EntityKindDialog({
  card,
  client,
  onClose,
  onSaved,
  onReload,
  onMergedAway,
}: EntityKindDialogProps): JSX.Element {
  const groupId = useId();
  const [choice, setChoice] = useState<EntityTypeValue | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<EntityEditError | "generic" | null>(null);
  const [mergeInto, setMergeInto] = useState<EntityCard | null>(null);
  const [mergeLoadFailed, setMergeLoadFailed] = useState(false);

  // The 409 carries no version and the merge needs both, so load the survivor first.
  const offerMerge = async (survivorId: string): Promise<void> => {
    setBusy(true);
    setMergeLoadFailed(false);
    try {
      setMergeInto(await getEntity(client, survivorId));
    } catch {
      // Keep the clash message and its "View that entry" link; say the merge could not open.
      setMergeLoadFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const current = (card.type || "CONCEPT").toUpperCase(); // a blank kind is a concept
  const choices = KIND_CHOICES.filter((k) => k.value !== current);
  const oldKind = withArticle(kindNoun(card.type));

  const onSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (busy || choice === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await editEntity(client, card.entity_id, {
        entity_type: choice,
        version: card.version ?? 1,
      });
      onSaved(updated);
    } catch (err) {
      setError(parseEntityEditError(err) ?? "generic");
    } finally {
      setBusy(false);
    }
  };

  if (mergeInto !== null) {
    return (
      <EntityMergeDialog
        entry={mergeInto}
        presetOther={card}
        client={client}
        onClose={onClose}
        onReload={onReload}
        onMerged={(res) => onMergedAway?.(res.entry.entity_id)}
      />
    );
  }

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <form className="flex flex-col gap-3" onSubmit={(e) => void onSubmit(e)}>
          <DialogHeader>
            <DialogTitle id={`${groupId}-title`}>It&apos;s not {oldKind}, it&apos;s a…</DialogTitle>
          </DialogHeader>

          <RadioGroup
            aria-labelledby={`${groupId}-title`}
            id={groupId}
            value={choice ?? ""}
            onValueChange={(v) => setChoice(v as EntityTypeValue)}
          >
            {choices.map((k) => {
              const id = `${groupId}-${k.value}`;
              return (
                <div key={k.value} className="flex items-center gap-2">
                  <RadioGroupItem id={id} value={k.value} />
                  <Label htmlFor={id} className="font-normal">
                    {k.noun.charAt(0).toUpperCase() + k.noun.slice(1)}
                  </Label>
                </div>
              );
            })}
          </RadioGroup>

          <p className="m-0 text-xs text-muted-foreground">
            Lumogis will remember this: when it reads “{card.name}” as {oldKind} again, it will
            store it as the kind you pick. To undo, change it back.
          </p>

          {error !== null && (
            <KindErrorMessage
              error={error}
              onReload={onReload}
              onMerge={(id) => void offerMerge(id)}
              busy={busy}
            />
          )}
          {mergeLoadFailed && (
            <KindAlert>Couldn&apos;t open that entry to merge. Try again.</KindAlert>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || choice === null}>
              {busy ? "Saving…" : "Change"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KindErrorMessage({
  error,
  onReload,
  onMerge,
  busy,
}: {
  error: EntityEditError | "generic";
  onReload?: () => void;
  onMerge: (survivorId: string) => void;
  busy: boolean;
}): JSX.Element {
  if (error !== "generic" && error.code === "version_conflict") {
    return (
      <KindAlert>
        This entry changed since you opened it. Reload and try again.{" "}
        {onReload && (
          <Button type="button" variant="outline" size="sm" onClick={onReload}>
            Reload
          </Button>
        )}
      </KindAlert>
    );
  }
  if (error !== "generic" && error.code === "name_in_use") {
    const noun = withArticle(kindNoun(error.conflictingEntityType));
    const quoted = error.conflictingName ? `“${error.conflictingName}”` : "that name";
    // The clash is on another name of this entry, not its main name: say which.
    const otherName =
      error.name !== null &&
      error.conflictingName !== null &&
      error.name.toLowerCase() !== error.conflictingName.toLowerCase()
        ? `, which also goes by “${error.name}”`
        : "";
    return (
      <KindAlert>
        {error.conflictingIsStaged
          ? `Lumogis has ${noun} called ${quoted} that it isn't sure about yet.`
          : `You already have ${noun} called ${quoted}${otherName}.`}{" "}
        {error.conflictingEntityId && !error.conflictingIsStaged && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onMerge(error.conflictingEntityId!)}
          >
            Merge with {error.conflictingName ? `“${error.conflictingName}”` : "that entry"}
          </Button>
        )}{" "}
        {error.conflictingEntityId && (
          <Link
            to={`/entities/${encodeURIComponent(error.conflictingEntityId)}`}
            className="text-accent-text underline underline-offset-2"
          >
            View that entry
          </Link>
        )}
      </KindAlert>
    );
  }
  return (
    <KindAlert>Couldn&apos;t save. Try again.</KindAlert>
  );
}

function KindAlert({ children }: { children: ReactNode }): JSX.Element {
  return (
    <Alert variant="destructive">
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}
