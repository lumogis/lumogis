// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-793 — failed-send recovery.
//
// Before this, a send that failed was recoverable only while the composer was
// open. The message was durable in `action_proposals.payload` from the moment
// the user pressed Review, but `list_pending` filters `status = 'pending'` and a
// failed send is `dead`, so it appeared on no screen at all.
//
// Two decisions from the ADR are visible in this file, and both are deliberate:
//
// 1. **Recovery writes a draft to the mailbox; it does not restore into the
//    composer.** `EmailComposerProps` has no seeding props, and an in-app
//    restore would fail again on the same broken SMTP path. A draft in the real
//    Drafts folder can be sent from Thunderbird, a phone, anywhere.
//
// 2. **The outcome is stated, never hedged.** "This may have been sent" is
//    unacceptable when Lumogis is the thing holding the send log. The server
//    resolves it and this renders the answer — including `partially_sent`,
//    which still offers recovery, because the refused recipients did not get it.

import { useCallback, useEffect, useState } from "react";
import type { JSX } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/util/formatTimestamp";
import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import type { RecoverableSendItem, SendOutcomeState } from "../../api/approvals";
import {
  dismissRecoverableSend,
  listRecoverableSends,
  saveRecoverableSendToDrafts,
} from "../../api/approvals";

const BODY_PREVIEW_CHARS = 240;

function actionLabel(actionName: string): string {
  if (actionName === "email_forward") return "Forward";
  if (actionName === "email_reply") return "Reply";
  return "Message";
}

/** A timestamp with its DATE, not just its weekday.
 *
 * The weekday alone was wrong twice over. A failed send is listed for 30 days,
 * so "failed Mon" is ambiguous across four Mondays; and the attachment grace is
 * exactly 7 days, so `expires_at` always falls on the **same weekday** as
 * `failed_at` — the row read "failed Mon 6:00 PM … available until Mon 6:00 PM",
 * which looks like a window that has already closed.
 */
/** A timestamp with its DATE, not just its weekday.
 *
 * The weekday alone was wrong twice over. A failed send is listed for 30 days,
 * so "failed Mon" is ambiguous across four Mondays; and the attachment grace is
 * exactly 7 days, so `expires_at` always falls on the **same weekday** as
 * `failed_at` — the row read "failed Mon 6:00 PM … available until Mon 6:00 PM",
 * which looks like a window that has already closed. `formatDateTime` always
 * carries the date.
 */
function formatWhen(iso: string | null): string {
  return iso ? formatDateTime(iso) : "";
}

/** A deadline. Date only — the hour an attachment sweep happens to run is noise
 * the user cannot act on, and a bare time invites reading it as precise. */
function formatDeadline(iso: string | null): string {
  return iso ? formatDateTime(iso, { withTime: false }) : "";
}

/** The outcome line. Stated plainly — this is the sentence the whole feature
 * exists to be able to write. */
function OutcomeBadge({ item }: { item: RecoverableSendItem }): JSX.Element {
  const when = formatWhen(item.sent_at);
  switch (item.outcome as SendOutcomeState) {
    case "sent":
      return (
        <p className="m-0 text-sm" role="note">
          <span aria-hidden="true">✅</span> Sent{when ? ` ${when}` : ""} — nothing to recover.
        </p>
      );
    case "partially_sent":
      return (
        <p className="m-0 text-sm font-medium text-amber-700 dark:text-amber-400" role="note">
          <span aria-hidden="true">⚠️</span> Partly sent{when ? ` ${when}` : ""}.{" "}
          {item.delivered_to.length > 0 ? `${item.delivered_to.join(", ")} received it. ` : ""}
          {item.refused.length > 0 ? `${item.refused.join(", ")} did not.` : ""}
        </p>
      );
    case "unknown":
      return (
        <p className="m-0 text-sm font-medium text-amber-700 dark:text-amber-400" role="note">
          <span aria-hidden="true">❓</span> Couldn&rsquo;t confirm whether this was sent.
        </p>
      );
    default:
      return (
        <p className="m-0 text-sm font-medium text-destructive" role="note">
          <span aria-hidden="true">⚠️</span> Not sent.
        </p>
      );
  }
}

/** Attachment availability, stated from what is actually still on disk. */
function Attachments({ item }: { item: RecoverableSendItem }): JSX.Element | null {
  const { count, available, expires_at } = item.attachments;
  if (count === 0 && !available) return null;
  const until = formatDeadline(expires_at);
  return (
    <p className="m-0 text-sm text-muted-foreground" data-testid="recovery-attachments">
      {count} file{count === 1 ? "" : "s"}
      {until ? ` · available until ${until}` : ""}
    </p>
  );
}

function RecoverableSendRow({
  item,
  client,
  onGone,
}: {
  item: RecoverableSendItem;
  client: ApiClient;
  onGone: (proposalId: number) => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // A successful save retires the button. The row deliberately STAYS listed —
  // dismissing it is the user's call, not ours — but leaving Save live invites
  // a second click that writes a second identical draft to the real mailbox.
  // `busy` alone does not cover it: it only spans the in-flight request.
  const [saved, setSaved] = useState(false);
  // Dismissing throws the recoverable text away, so it is confirmed first.
  const [confirmDismiss, setConfirmDismiss] = useState(false);

  const longBody = item.body.length > BODY_PREVIEW_CHARS;
  const shown = expanded || !longBody ? item.body : `${item.body.slice(0, BODY_PREVIEW_CHARS)}…`;

  const onSave = useCallback(async () => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await saveRecoverableSendToDrafts(client, item.proposal_id);
      if (!res.success) {
        // `no_drafts_folder` is the known unmet acceptance criterion — say so
        // rather than pretending the save worked.
        setMessage(
          res.code === "no_drafts_folder"
            ? "This mailbox has no Drafts folder, so it couldn't be saved there."
            : `Couldn't save to Drafts: ${res.error ?? "unknown error"}`,
        );
        return;
      }
      setSaved(true);
      setMessage(
        res.attachments_dropped > 0
          ? `Saved to Drafts — ${res.attachments_dropped} file${
              res.attachments_dropped === 1 ? " was" : "s were"
            } no longer available, you'll need to re-attach.`
          : "Saved to Drafts.",
      );
    } catch (err) {
      // A 409 means the server re-resolved the row as sent while this page sat
      // open. That is the guard working, not a bug — explain it.
      //
      // Keyed on `ApiError.status`, never on the message text: `message` is
      // `HTTP ${status}: ${detail}` and `detail` is the stringified body, so a
      // substring test for "409" would also fire on a proposal id or a
      // timestamp that happens to contain it.
      if (err instanceof ApiError && err.status === 409) {
        setMessage("This message was actually sent, so there's nothing to recover.");
      } else {
        const text = err instanceof Error ? err.message : String(err);
        setMessage(`Couldn't save to Drafts: ${text}`);
      }
    } finally {
      setBusy(false);
    }
  }, [client, item.proposal_id]);

  const onDismiss = useCallback(async () => {
    setBusy(true);
    try {
      await dismissRecoverableSend(client, item.proposal_id);
      onGone(item.proposal_id);
    } catch {
      setMessage("Couldn't dismiss this. It's still here.");
      setBusy(false);
    }
  }, [client, item.proposal_id, onGone]);

  return (
    <li
      className="flex flex-col gap-2 rounded-lg border bg-card p-3 text-card-foreground"
      data-testid="recoverable-send"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
        <span className="font-semibold">{actionLabel(item.action_name)}</span>
        {item.to.length > 0 ? <span className="break-all">to {item.to.join(", ")}</span> : null}
        {item.failed_at ? (
          <span className="text-muted-foreground">failed {formatWhen(item.failed_at)}</span>
        ) : null}
      </div>

      {item.subject ? <p className="m-0 font-medium">{item.subject}</p> : null}

      <OutcomeBadge item={item} />

      {/* Seeing the text is the reassurance. It is read-only and always
          present — a summary would defeat the point. */}
      <p className="m-0 text-sm whitespace-pre-wrap break-words" data-testid="recovery-body">
        {shown}
      </p>
      {longBody ? (
        <div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "Show less" : "Show all"}
          </Button>
        </div>
      ) : null}

      <Attachments item={item} />

      <div className="flex flex-wrap gap-2">
        {/* `sent` offers no save — there is genuinely nothing to recover. A
            PARTIAL still does: those refused recipients never got it. And once
            saved, the button retires so a second click cannot write a second
            copy of the same draft into the mailbox. */}
        {item.outcome !== "sent" && !saved ? (
          <Button type="button" size="sm" onClick={onSave} disabled={busy}>
            Save to Drafts
          </Button>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setConfirmDismiss(true)}
          disabled={busy}
        >
          Dismiss
        </Button>
      </div>

      {message ? (
        <p className="m-0 text-sm" role="status">
          {message}
        </p>
      ) : null}

      <AlertDialog open={confirmDismiss} onOpenChange={setConfirmDismiss}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Dismiss this message?</AlertDialogTitle>
            <AlertDialogDescription>
              It didn&rsquo;t send, and dismissing removes it from this list, so the text will no
              longer be kept here. Use Save to Drafts first if you want to keep it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setConfirmDismiss(false);
                void onDismiss();
              }}
            >
              Dismiss
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}

/** Failed sends, with their text, their outcome, and one way to recover them. */
export function RecoverableSends({ client }: { client: ApiClient }): JSX.Element | null {
  const [items, setItems] = useState<RecoverableSendItem[]>([]);

  useEffect(() => {
    const ctrl = new AbortController();
    listRecoverableSends(client, ctrl.signal)
      .then((res) => setItems(res.items))
      .catch(() => {
        /* best-effort: a recovery listing must never block the approvals page */
      });
    return () => ctrl.abort();
  }, [client]);

  const onGone = useCallback((proposalId: number) => {
    setItems((prev) => prev.filter((i) => i.proposal_id !== proposalId));
  }, []);

  if (items.length === 0) return null;

  return (
    <section className="flex flex-col gap-3" aria-label="Messages that didn't send">
      {/* Distinct from the "Unsent messages" section above, which lists
          proposals the user ABANDONED. These are ones that were sent and
          failed — a different thing that needs a different answer. */}
      <h2 className="m-0 text-base font-semibold">Messages that didn&rsquo;t send</h2>
      <ul className="m-0 flex list-none flex-col gap-3 p-0" role="list">
        {items.map((item) => (
          <RecoverableSendRow key={item.proposal_id} item={item} client={client} onGone={onGone} />
        ))}
      </ul>
    </section>
  );
}
