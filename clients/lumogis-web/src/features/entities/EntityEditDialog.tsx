// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// "Edit names" dialog (LUM-161) — rename an entry and edit its other names.
// Owner-only; the caller (EntityCardPanel) decides when to offer it.
//
// The dialog sends *deltas* against the card it opened with (plan Decision 8):
// `aliases_add` / `aliases_remove`, never the final list, so an alias that
// extraction added while the dialog was open survives a save. The old name is
// added by the server when the name changes; the member drops it by removing
// it from the list (it then goes into `aliases_remove`).
/* eslint-disable react-refresh/only-export-components */

import { useId, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { ApiClient } from "../../api/client";
import {
  editEntity,
  parseEntityEditError,
  type EntityCard,
  type EntityEditError,
  type EntityEditRequest,
} from "../../api/search";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
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

export interface EntityEditDialogProps {
  card: EntityCard;
  client: ApiClient;
  onClose: () => void;
  /** Called with the updated card after a successful save. */
  onSaved: (card: EntityCard) => void;
  /** Called when the member chooses "Reload" after a version conflict. */
  onReload?: () => void;
}

const lc = (s: string): string => s.toLowerCase();

function sameName(a: string, b: string): boolean {
  return lc(a.trim()) === lc(b.trim());
}

/** Build the PATCH body from the card the dialog opened with and the edited state. */
export function buildEditDelta(
  card: EntityCard,
  name: string,
  names: string[],
  oldNameRemoved: boolean,
): EntityEditRequest {
  const trimmed = name.trim();
  const body: EntityEditRequest = { version: card.version ?? 1 };
  if (trimmed !== card.name) body.name = trimmed;

  const original = card.aliases;
  const add = names.filter(
    (n) => !original.some((o) => sameName(o, n)) && !sameName(n, card.name),
  );
  const remove = original.filter((o) => !names.some((n) => sameName(o, n)));
  const nameChanged = !sameName(trimmed, card.name);
  // A member who removes the kept old name and then types it back in wants it kept.
  const oldNameReAdded = names.some((n) => sameName(n, card.name));
  if (
    nameChanged &&
    oldNameRemoved &&
    !oldNameReAdded &&
    !remove.some((r) => sameName(r, card.name))
  ) {
    remove.push(card.name);
  }
  body.aliases_add = add;
  body.aliases_remove = remove;
  return body;
}

export function EntityEditDialog({
  card,
  client,
  onClose,
  onSaved,
  onReload,
}: EntityEditDialogProps): JSX.Element {
  const nameId = useId();
  const addId = useId();
  const [name, setName] = useState(card.name);
  const [names, setNames] = useState<string[]>(card.aliases);
  const [oldNameRemoved, setOldNameRemoved] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<EntityEditError | "generic" | null>(null);

  const nameChanged = name.trim() !== "" && !sameName(name, card.name);
  const body = useMemo(
    () => buildEditDelta(card, name, names, oldNameRemoved),
    [card, name, names, oldNameRemoved],
  );
  const hasRemoval = (body.aliases_remove?.length ?? 0) > 0;
  const hasChange =
    body.name !== undefined ||
    (body.aliases_add?.length ?? 0) > 0 ||
    (body.aliases_remove?.length ?? 0) > 0;

  const addDraft = (): void => {
    const v = draft.trim().replace(/\s+/g, " ");
    if (!v) return;
    if (!names.some((n) => sameName(n, v)) && !sameName(v, name)) {
      setNames((prev) => [...prev, v]);
    }
    setDraft("");
  };

  const onSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (busy || !hasChange) return;
    if (name.trim() === "") {
      setError({ code: "empty_name" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await editEntity(client, card.entity_id, body);
      onSaved(updated);
    } catch (err) {
      setError(parseEntityEditError(err) ?? "generic");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <form className="flex flex-col gap-3" onSubmit={(e) => void onSubmit(e)}>
          <DialogHeader>
            <DialogTitle>Edit names</DialogTitle>
          </DialogHeader>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={nameId}>Name</Label>
            <Input
              id={nameId}
              type="text"
              value={name}
              maxLength={256}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <p className="m-0 text-sm font-medium">Other names</p>
            <ul role="list" className="m-0 flex list-none flex-col divide-y divide-border p-0">
              {nameChanged && !oldNameRemoved && (
                <li className="flex min-h-9 items-center gap-2 py-1">
                  <span className="min-w-0 flex-1 break-words">{card.name}</span>
                  <Badge variant="outline">kept</Badge>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${card.name}`}
                    onClick={() => setOldNameRemoved(true)}
                  >
                    Remove
                  </Button>
                </li>
              )}
              {names.map((n) => (
                <li key={n} className="flex min-h-9 items-center gap-2 py-1">
                  <span className="min-w-0 flex-1 break-words">{n}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${n}`}
                    onClick={() => setNames((prev) => prev.filter((x) => x !== n))}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={addId}>Add another name</Label>
            <div className="flex gap-2">
              <Input
                id={addId}
                type="text"
                value={draft}
                maxLength={256}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addDraft();
                  }
                }}
              />
              <Button type="button" variant="outline" onClick={addDraft}>
                Add
              </Button>
            </div>
          </div>

          {hasRemoval && (
            <p className="m-0 text-xs text-muted-foreground" role="note">
              If you remove a name, Lumogis may learn it again as a new entry the next time it
              reads it.
            </p>
          )}
          <p className="m-0 text-xs text-muted-foreground">
            The map may show the old name for up to a day.
          </p>

          {error !== null && <EditErrorMessage error={error} onReload={onReload} />}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !hasChange}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditErrorMessage({
  error,
  onReload,
}: {
  error: EntityEditError | "generic";
  onReload?: () => void;
}): JSX.Element {
  if (error === "generic") return <ErrorLine>Couldn't save. Try again.</ErrorLine>;
  switch (error.code) {
    case "version_conflict":
      return (
        <ErrorLine>
          This entry changed since you opened it. Reload and try again.{" "}
          {onReload && (
            <Button type="button" variant="outline" size="sm" onClick={onReload}>
              Reload
            </Button>
          )}
        </ErrorLine>
      );
    case "name_in_use": {
      const quoted = error.conflictingName ? `“${error.conflictingName}”` : "that name";
      return (
        <ErrorLine>
          {error.conflictingIsStaged
            ? `Lumogis has an entry called ${quoted} that it isn't sure about yet.`
            : `Another entry is already called ${quoted}.`}{" "}
          {error.conflictingEntityId && (
            <Link
              to={`/entities/${encodeURIComponent(error.conflictingEntityId)}`}
              className="text-accent-text underline underline-offset-2"
            >
              View that entry
            </Link>
          )}
        </ErrorLine>
      );
    }
    case "empty_name":
      return (
        <ErrorLine>
          The name can't be empty.
        </ErrorLine>
      );
    case "invalid_characters":
      return (
        <ErrorLine>
          Names can't contain control characters.
        </ErrorLine>
      );
    case "too_many_aliases":
      return (
        <ErrorLine>
          Too many other names (50 at most).
        </ErrorLine>
      );
    case "nothing_to_change":
      return (
        <ErrorLine>
          Nothing to change.
        </ErrorLine>
      );
    case "not_owner":
    case "not_editable":
      return (
        <ErrorLine>
          You can't edit this entry.
        </ErrorLine>
      );
    default:
      return (
        <ErrorLine>
          Couldn't save. Try again.
        </ErrorLine>
      );
  }
}

function ErrorLine({ children }: { children: ReactNode }): JSX.Element {
  return (
    <Alert variant="destructive">
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}
