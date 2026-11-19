// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Settings → Names Lumogis won't learn (LUM-966): the names a delete put on the
// member's list, her kind corrections ("it's not a person, it's an organisation"),
// and the shared entries she has hidden. Removing a name is the undo of a delete; it
// then offers to re-read the documents that mentioned the entry, because removing the
// name alone teaches Lumogis nothing about what is already in them.
// Rebuilt on the shared components in LUM-993 plan B Pass 6 (a section per list;
// "Remove" is the undo of a delete, so it needs no confirm).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { ApiClient } from "../../api/client";
import { reingestDocument } from "../../api/documents";
import {
  listNameRules,
  parseEntityEditError,
  removeNameRule,
  unhideEntity,
  type NameRuleBlock,
  type NameRuleCorrection,
  type RemoveNameRuleResponse,
  type RereadDocument,
} from "../../api/search";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/page/EmptyState";
import { ErrorState } from "../../components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "../../components/page/LoadingState";
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { kindNoun, withArticle } from "../entities/EntityKindDialog";

const QUERY_KEY = ["me", "name-rules"] as const;

type Offer = {
  /** New per removal, so a second removal remounts the offer with fresh state. */
  id: number;
  name: string;
  docs: RereadDocument[];
  more: number;
  restored: NameRuleCorrection[];
};

type DocStatus = "queued" | "failed";

export function MeWontLearnView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [offer, setOffer] = useState<Offer | null>(null);

  const listQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => listNameRules(client, signal),
  });

  const refresh = (): void => {
    void qc.invalidateQueries({ queryKey: QUERY_KEY });
    void qc.invalidateQueries({ queryKey: ["kg", "entities"] });
  };

  const removeM = useMutation({
    mutationFn: (v: { kind: "block" | "retype"; name_norm: string; entity_type: string; label: string }) =>
      removeNameRule(client, { kind: v.kind, name_norm: v.name_norm, entity_type: v.entity_type }),
    onSuccess: (res: RemoveNameRuleResponse, v) => {
      setErr(null);
      if (v.kind === "block" && (res.reread_documents.length > 0 || res.restored_corrections.length > 0)) {
        setOffer((prev) => ({
          id: (prev?.id ?? 0) + 1,
          name: v.label,
          docs: res.reread_documents,
          more: res.reread_more,
          restored: res.restored_corrections,
        }));
      } else {
        setOffer(null);
      }
      refresh();
    },
    onError: (e) => {
      const parsed = parseEntityEditError(e);
      if (parsed && parsed.code === "rule_not_found") {
        setErr("Already removed.");
        refresh();
      } else {
        setErr("Couldn't remove that. Try again.");
      }
    },
  });

  const showAgainM = useMutation({
    mutationFn: (entityId: string) => unhideEntity(client, entityId),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: () => setErr("Couldn't show that again. Try again."),
  });

  const data = listQ.data;

  const listClass = "m-0 flex list-none flex-col divide-y divide-border p-0";
  const itemClass = "flex min-h-12 flex-wrap items-center justify-between gap-x-3 gap-y-1 py-1.5 text-sm";

  return (
    <section data-testid="me-wont-learn">
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        When you delete an entry in What Lumogis knows, Lumogis keeps its names here so a
        re-read doesn&apos;t bring it back. Remove a name to let Lumogis learn it again.
      </p>

      {err && (
        <Alert variant="destructive" className="mb-4 max-w-2xl">
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      )}
      {listQ.isPending && (
        <LoadingPlaceholder label="Loading your list…" className="max-w-2xl">
          <SkeletonText lines={5} />
        </LoadingPlaceholder>
      )}
      {listQ.isError && (
        <ErrorState message="Lumogis couldn't load your list." onRetry={() => void listQ.refetch()} />
      )}

      {offer && <RereadOffer key={offer.id} offer={offer} client={client} onDismiss={() => setOffer(null)} />}

      {data && (
        <>
          <PageSection title="Names Lumogis won't learn">
            {data.blocked.length === 0 ? (
              <EmptyState
                className="py-4"
                title="Nothing blocked"
                helperText="Nothing here. Names you delete from What Lumogis knows will appear on this list."
              />
            ) : (
              <ul className={listClass} role="list">
                {data.blocked.map((b) => (
                  <BlockRow
                    key={`${b.name_norm}:${b.entity_type}`}
                    block={b}
                    busy={removeM.isPending}
                    onRemove={() =>
                      removeM.mutate({
                        kind: "block",
                        name_norm: b.name_norm,
                        entity_type: b.entity_type,
                        label: b.display_name,
                      })
                    }
                  />
                ))}
              </ul>
            )}
            {data.blocked_total > data.blocked.length && (
              <p className="m-0 mt-2 text-xs text-muted-foreground">
                Showing {data.blocked.length} of {data.blocked_total}
              </p>
            )}
            <p className="m-0 mt-3 text-xs text-muted-foreground">
              Chats and notes are not re-read; only documents can be.
            </p>
          </PageSection>

          <PageSection title="Kind corrections">
            {data.corrections.length === 0 ? (
              <EmptyState
                className="py-4"
                title="No corrections"
                helperText="You haven't corrected what kind of thing anything is. Use “This is wrong…” on an entry to do that."
              />
            ) : (
              <ul className={listClass} role="list">
                {data.corrections.map((c) => (
                  <li key={`${c.name_norm}:${c.from_type}`} className={itemClass}>
                    <span className="min-w-0 break-words">
                      {c.display_name}: {kindNoun(c.from_type)} → {kindNoun(c.to_type)}
                    </span>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={removeM.isPending}
                      aria-label={`Remove kind correction for ${c.display_name}`}
                      onClick={() =>
                        removeM.mutate({
                          kind: "retype",
                          name_norm: c.name_norm,
                          entity_type: c.from_type,
                          label: c.display_name,
                        })
                      }
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            {data.corrections_total > data.corrections.length && (
              <p className="m-0 mt-2 text-xs text-muted-foreground">
                Showing {data.corrections.length} of {data.corrections_total}
              </p>
            )}
          </PageSection>

          <PageSection title="Hidden from your list">
            {data.hidden.length === 0 ? (
              <EmptyState
                className="py-4"
                title="Nothing hidden"
                helperText="Nothing hidden. Use “Hide from my list” on a household entry to hide it from you only."
              />
            ) : (
              <ul className={listClass} role="list">
                {data.hidden.map((h) => (
                  <li key={h.entity_id} className={itemClass}>
                    <Link
                      className="min-w-0 break-words text-accent-text underline underline-offset-2"
                      to={`/entities/${encodeURIComponent(h.entity_id)}`}
                    >
                      {h.name}
                    </Link>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={showAgainM.isPending}
                      onClick={() => showAgainM.mutate(h.entity_id)}
                    >
                      Show again
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            {data.hidden_total > data.hidden.length && (
              <p className="m-0 mt-2 text-xs text-muted-foreground">
                Showing {data.hidden.length} of {data.hidden_total}
              </p>
            )}
          </PageSection>
        </>
      )}
    </section>
  );
}

function BlockRow({
  block,
  busy,
  onRemove,
}: {
  block: NameRuleBlock;
  busy: boolean;
  onRemove: () => void;
}): JSX.Element {
  return (
    <li className="flex min-h-12 flex-wrap items-center justify-between gap-x-3 gap-y-1 py-1.5 text-sm">
      <span className="min-w-0 break-words">
        {block.display_name} as {withArticle(kindNoun(block.entity_type))}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        aria-label={`Remove ${block.display_name}`}
        onClick={onRemove}
      >
        Remove
      </Button>
    </li>
  );
}

/** After removing a name: put a kind correction back, and offer to re-read the documents. */
function RereadOffer({
  offer,
  client,
  onDismiss,
}: {
  offer: Offer;
  client: ApiClient;
  onDismiss: () => void;
}): JSX.Element {
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [status, setStatus] = useState<Record<number, DocStatus>>({});

  const run = async (): Promise<void> => {
    setRunning(true);
    // One at a time: each re-read is a queued job, and a failing one must not stop the rest.
    for (const doc of offer.docs) {
      try {
        await reingestDocument(client, doc.document_id, { force: true });
        setStatus((s) => ({ ...s, [doc.document_id]: "queued" }));
      } catch {
        setStatus((s) => ({ ...s, [doc.document_id]: "failed" }));
      }
    }
    setRunning(false);
    setDone(true);
  };

  const n = offer.docs.length;
  return (
    <Alert role="status" data-testid="reread-offer" className="mb-4 max-w-2xl">
      <AlertDescription className="flex flex-col gap-2">
        <p className="m-0">Removed “{offer.name}”. Lumogis can learn it again.</p>
        {offer.restored.map((c) => (
          <p key={`${c.name_norm}:${c.from_type}`} className="m-0">
            Your kind correction for “{c.display_name}” applies again.
          </p>
        ))}
        {n > 0 && !done && (
          <>
            <p className="m-0">
              Re-read {n} document{n === 1 ? "" : "s"} that mentioned it?
              {offer.more > 0 && ` And ${offer.more} more — re-read them from Documents.`}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" disabled={running} onClick={() => void run()}>
                {running ? "Re-reading…" : "Re-read"}
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={running} onClick={onDismiss}>
                Not now
              </Button>
            </div>
          </>
        )}
        {n > 0 && (done || running) && (
          <ul className="m-0 flex list-none flex-col divide-y divide-border p-0" role="list">
            {offer.docs.map((d) => (
              <li key={d.document_id} className="flex flex-wrap justify-between gap-2 py-1">
                <span className="min-w-0 break-words">{d.title}</span>
                <span>
                  {status[d.document_id] === "queued" && "Queued"}
                  {status[d.document_id] === "failed" && "Couldn't re-read this one"}
                </span>
              </li>
            ))}
          </ul>
        )}
        {(done || n === 0) && (
          <div>
            <Button type="button" size="sm" onClick={onDismiss}>
              Done
            </Button>
          </div>
        )}
      </AlertDescription>
    </Alert>
  );
}
