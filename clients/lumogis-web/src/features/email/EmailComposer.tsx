// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-652: the send composer. A native <dialog> (built-in focus-trap / Escape)
// with an embedded "In reply to:" context header so the modal never hides what
// you're replying to. Two states in one frame: compose → confirm (SendConfirmCard).
//
// Lifecycle (matches the plan): "Review" creates the send proposal (the Ask, which
// carries first_contact); [Send] approves+executes it; [Save to Drafts] creates its
// own email_save_draft proposal. A failed proposal is dead-lettered, so [Retry] and
// the Save-to-Drafts fallback build a FRESH proposal — never re-execute a dead row.
// A draft populated asynchronously never overwrites text the user already typed.
//
// The proposal/draft API calls are injected (ComposerApi) so the flow is unit-
// testable without a live client; EmailInboxView supplies the real bindings.

import { useEffect, useRef, useState } from "react";

import { VoiceDictationButton } from "../../components/VoiceDictationButton";
import { AttachmentPicker } from "./AttachmentPicker";
import { RecipientInput } from "./RecipientInput";
import { SendConfirmCard, type SendStatus } from "./SendConfirmCard";
import { formatBytes } from "../../api/emailAttachments";
import type { AttachmentRow, StagedAttachment } from "../../api/emailAttachments";
import type { RecipientSuggestion } from "../../api/emailRecipients";

/** No client wired: behave exactly as before autocomplete existed. */
const _noSuggestions = async (): Promise<RecipientSuggestion[]> => [];

/** LUM-817 — the signature block a pristine body carries, as seeded below. */
function signatureBlock(sig: string): string {
  return sig ? `\n\n${sig}` : "";
}

/** True when the body still holds nothing the user wrote.
 *
 * LUM-817: this exists because seeding a signature makes the body non-empty,
 * and the reply-draft anti-clobber below fills only an EMPTY body. Without
 * this, turning on a signature would silently stop the AI draft from ever
 * appearing — a feature disabling another feature, with no error anywhere. */
function isPristine(current: string, sig: string): boolean {
  return current === "" || current === signatureBlock(sig);
}

export interface ComposerApi {
  /** LUM-817 — the mailbox's outgoing signature, seeded into the visible body. */
  fetchSignature?: (sourceId: string) => Promise<string>;
  /** LUM-818 — recipient completion. Optional so existing callers keep working;
   *  when absent the fields behave exactly as they did before. */
  suggestRecipients?: (query: string, signal?: AbortSignal) => Promise<RecipientSuggestion[]>;
  createProposal: (
    actionName: string,
    payload: Record<string, unknown>,
  ) => Promise<{ proposal_id: number; first_contact: boolean }>;
  approveProposal: (id: number) => Promise<unknown>;
  executeProposal: (
    id: number,
  ) => Promise<{ success: boolean; error: string | null; code: string | null }>;
  /** Abandon an unsent proposal (LUM-682). Best-effort. */
  cancelProposal?: (id: number) => Promise<unknown>;
  /** Optional on-box draft suggestion (reply only). */
  fetchReplyDraft?: (signalId: string) => Promise<string>;
  /** LUM-698: the quoted original, shown in its own editable field so the user
   * can see and trim what they send. Lazy in reply mode (collapsed by default),
   * eager in forward mode (the quoted content IS the message). */
  fetchQuote?: (signalId: string, style: "reply" | "forward") => Promise<string>;
  /** Optional voice dictation (LUM-653). When present, a mic button appears in
   * the compose phase and appends the transcript to the body. */
  transcribeVoice?: (blob: Blob) => Promise<string>;
  /** Optional on-box tone/length refine (LUM-684). When present, a refine row
   * appears in the compose phase and offers a non-destructive preview. */
  refine?: (req: {
    text: string;
    preset?: string;
    instruction?: string;
  }) => Promise<{ text: string; available: boolean }>;
  /** LUM-695: the ApiClient the attachment picker stages through. When absent
   * the picker is hidden and the composer behaves exactly as before. */
  attachmentClient?: unknown;
  /** LUM-712: the original's attachment list, for the forward-mode picker. */
  listAttachments?: (signalId: string) => Promise<{
    rows: AttachmentRow[];
    available: boolean;
  }>;
}

/** The preset refine chips (server owns the actual prompt wording). */
const REFINE_PRESETS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "formal", label: "Formal" },
  { key: "shorter", label: "Shorter" },
  { key: "longer", label: "Longer" },
  { key: "clearer", label: "Clearer" },
  { key: "friendlier", label: "Friendlier" },
  { key: "fix_grammar", label: "Fix grammar" },
];

export interface EmailComposerProps {
  open: boolean;
  mode: "reply" | "compose" | "forward";
  /** Reply context. */
  signalId?: string;
  replyRecipient?: string; // the original sender = the reply recipient
  originalSubject?: string;
  /** LUM-705: Cc addresses to pre-fill (reply-all). Non-empty ALSO expands the
   *  Cc/Bcc disclosure — a pre-filled field the user cannot see is how this
   *  feature mails four people by surprise. Both are mount-constant. */
  initialCc?: string[];
  /** Compose context. */
  sourceId?: string;
  /** LUM-830: the mailboxes this user can send FROM, for `mode="compose"`.
   *
   * A reply inherits its mailbox from the message it answers; a composed
   * message has no originating signal, so it must choose one — this is the
   * first surface in the product that has to (ADR-213). The host supplies only
   * mailboxes that can actually send: offering one with sending off would
   * reproduce LUM-719, an affordance that fails at the send.
   *
   * Ignored outside compose mode. Absent or empty falls back to `sourceId`, so
   * a host that does not know about mailboxes behaves exactly as before. */
  mailboxes?: ReadonlyArray<{ id: string; name: string; address?: string }>;
  /** Display-only "from" label. */
  fromAddress?: string | null;
  /** LUM-831: the mailbox NAME, used only when the address could not be
   * resolved. Reply and forward have no From picker, so without this the
   * confirm card had nothing to fall back to but a placeholder — and the ADR's
   * promise that an undecryptable mailbox shows *name-only* was unreachable
   * outside compose mode. */
  fromName?: string | null;
  /** LUM-831: the addresses could not be loaded and there is more than one
   * mailbox to choose between, so the picker is back to bare labels. */
  addressesDegraded?: boolean;
  api: ComposerApi;
  onClose: () => void;
}

type Phase = "compose" | "confirm";

/** LUM-831: `Name — address`, falling back to the bare name.
 *
 * Not the RFC-ish `Name <address>`: that form reads as a *person's* name, and a
 * Lumogis mailbox name is a label the user typed — `Work <alice@acme.com>`
 * reads as a person called "Work". */
function mailboxLabel(m: { name?: string | null; address?: string | null }): string {
  const name = (m.name ?? "").trim();
  const addr = (m.address ?? "").trim();
  if (name && addr) return `${name} — ${addr}`;
  return addr || name;
}

function fwdSubject(subject: string | undefined): string {
  const s = (subject ?? "").trim();
  if (!s) return "Fwd:";
  const low = s.toLowerCase();
  return low.startsWith("fwd:") || low.startsWith("fw:") ? s : `Fwd: ${s}`;
}

/** Split a comma-separated recipient input into bare addr-specs. The backend's
 * `_validate_recipient` rejects whitespace, so display-name forms are invalid —
 * a pasted "Jane <j@x.com>" surfaces as a 422 at proposal create. */
function splitAddrs(raw: string): string[] {
  return raw
    .split(",")
    .map((a) => a.trim())
    .filter(Boolean);
}

function reSubject(subject: string | undefined): string {
  const s = (subject ?? "").trim();
  if (!s) return "Re:";
  return /^re:/i.test(s) ? s : `Re: ${s}`;
}

export function EmailComposer(props: EmailComposerProps): JSX.Element | null {
  const { open, mode, signalId, replyRecipient, originalSubject, sourceId, api, initialCc } = props;
  const addressesDegraded = props.addressesDegraded ?? false;
  const mailboxes = props.mailboxes ?? [];

  const dialogRef = useRef<HTMLDialogElement | null>(null);
  // LUM-830 — which mailbox a composed message leaves from.
  //
  // Only compose mode has a choice to make: a reply and a forward both descend
  // from a message that already names its mailbox, so they keep taking
  // `sourceId` from the host and this state is inert for them.
  const [chosenSource, setChosenSource] = useState(() => sourceId ?? mailboxes[0]?.id ?? "");
  // Reconciled against a LATER `mailboxes` prop, not just the mount-time one: a
  // credentials refetch can drop a mailbox while the composer is open, and a
  // selection pointing at one no longer offered renders an empty From row while
  // the payload still carries the dead id. Only ever corrects an id that is
  // absent from the list, so it can never override a valid choice the user made.
  const stillOffered = mailboxes.some((m) => m.id === chosenSource);
  const activeSourceId =
    mode === "compose" ? (stillOffered ? chosenSource : (mailboxes[0]?.id ?? "")) : sourceId;
  // LUM-831: the confirm card gets `Name — address` when the address resolved,
  // so the last screen before an irreversible send names the account.
  const chosen = mailboxes.find((m) => m.id === activeSourceId);
  const chosenMailboxName = chosen ? mailboxLabel(chosen) : "";
  const [phase, setPhase] = useState<Phase>("compose");
  const [body, setBody] = useState("");
  const [bodyTouched, setBodyTouched] = useState(false);
  // LUM-817. Seeded into the VISIBLE body rather than appended at send time, so
  // the user can see it and delete it for one message. That also makes the
  // composer the single application point — a server-side append on top of this
  // would put the signature on every message twice.
  const [signature, setSignature] = useState("");
  // Always holds the LIVE signature. The reply-draft effect's async callback
  // captures `signature` at effect-creation time — "" — so it would judge a
  // body that already holds the seeded signature as "not pristine" and drop the
  // draft on the floor. Same shape, and same fix, as `bodyRef` above.
  const signatureRef = useRef(signature);
  signatureRef.current = signature;
  const [drafting, setDrafting] = useState(false);
  const [dictating, setDictating] = useState(false);
  // Refine (LUM-684): non-destructive preview state.
  const [refining, setRefining] = useState(false);
  const [refinePreview, setRefinePreview] = useState<string | null>(null);
  const [preRefineBody, setPreRefineBody] = useState<string | null>(null);
  const [refineUnavailable, setRefineUnavailable] = useState(false);
  const [refineError, setRefineError] = useState<string | null>(null);
  const [customInstruction, setCustomInstruction] = useState("");
  // Always holds the LIVE body so an in-flight refine can detect that the user
  // typed/dictated meanwhile (the async closure's `body` is stale by then).
  const bodyRef = useRef(body);
  bodyRef.current = body;
  const [to, setTo] = useState(mode === "reply" ? (replyRecipient ?? "") : "");
  const [subject, setSubject] = useState(
    mode === "reply"
      ? reSubject(originalSubject)
      : mode === "forward"
        ? fwdSubject(originalSubject)
        : "",
  );
  // LUM-698. Comma-separated inputs, mirroring how `to` already works.
  // LUM-705: seeded from `initialCc` at MOUNT. The composer is keyed per signal,
  // so a fresh reply-all gets a fresh mount and this runs exactly once.
  const [cc, setCc] = useState(() => (initialCc ?? []).join(", "));
  const [bcc, setBcc] = useState("");
  // The quoted original lives in its OWN state/field, deliberately not merged
  // into `body`: `body` already has four writers (typing, draft anti-clobber,
  // voice append, refine apply) and adding a fifth async entrant would race
  // them. Keeping it separate also makes "trim the quote" first-class and keeps
  // refine's 8000-char input cap off the quoted thread.
  const [quote, setQuote] = useState("");
  const [quoteRequested, setQuoteRequested] = useState(false);
  // LUM-695: staged attachment REFERENCES. Only the ids reach the payload —
  // bytes are already on disk server-side by the time they land here.
  const [staged, setStaged] = useState<StagedAttachment[]>([]);
  // LUM-712: the ORIGINAL's own attachments, and which of them the user has
  // chosen to carry. Indexes — not bytes — are what reach the payload.
  const [originalAttachments, setOriginalAttachments] = useState<AttachmentRow[]>([]);
  const [forwardPartIndexes, setForwardPartIndexes] = useState<number[]>([]);

  const [sendStatus, setSendStatus] = useState<SendStatus>("idle");
  const [successKind, setSuccessKind] = useState<"sent" | "saved" | null>(null);
  const [firstContact, setFirstContact] = useState(false);
  const [sendProposalId, setSendProposalId] = useState<number | null>(null);
  const [failureCode, setFailureCode] = useState<string | null>(null);
  const [failureMessage, setFailureMessage] = useState<string | null>(null);

  // Open/close the native dialog imperatively. Prefer showModal() for the built-in
  // focus-trap/backdrop; fall back to the `open` attribute where showModal is a
  // no-op (jsdom) so the contents stay in the accessibility tree.
  useEffect(() => {
    const dlg = dialogRef.current;
    if (!dlg) return;
    if (open) {
      if (typeof dlg.showModal === "function") {
        try {
          if (!dlg.open) dlg.showModal();
        } catch {
          /* already open / unsupported — fall through */
        }
      }
      if (!dlg.open) dlg.open = true;
    } else if (dlg.open) {
      if (typeof dlg.close === "function") dlg.close();
      dlg.open = false;
    }
  }, [open]);

  // LUM-698: fetch the quoted original. EAGER in forward mode (the disclosure is
  // open by default and the quoted content is the message); LAZY in reply mode —
  // gated on `quoteRequested`, set when the user first expands the disclosure —
  // so an ordinary reply-composer open does NOT pay a second IMAP round-trip for
  // a block that is collapsed by default.
  //
  // LUM-706: derived HERE rather than read inside the effect, which keeps
  // `quoteRequested` out of the dependency list. Forward renders the disclosure
  // already open, so the browser fires `toggle` on mount and the resulting
  // setQuoteRequested(true) used to re-run this effect and pay a second,
  // identical refetch — the exact round-trip the laziness exists to avoid,
  // reintroduced on the one path that never needed the flag. In forward mode
  // this is constant true, so the flag can no longer retrigger anything. jsdom
  // does not fire the native toggle, which is why 15 unit tests never saw it.
  const quoteWanted = mode === "forward" || (mode === "reply" && quoteRequested);
  useEffect(() => {
    if (!open || !signalId || !api.fetchQuote) return;
    const style = mode === "forward" ? "forward" : "reply";
    // No separate `mode === "compose"` guard: quoteWanted is false for compose
    // (it is neither forward nor reply), so that case has already returned —
    // tsc rejects the old check as unreachable.
    if (!quoteWanted) return;
    let cancelled = false;
    api
      .fetchQuote(signalId, style)
      .then((q) => {
        if (cancelled) return;
        // Only fill a still-untouched quote field (the user may have trimmed it).
        setQuote((current) => (current === "" ? q : current));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mode, signalId, quoteWanted]);

  // Async draft populate — never clobbers text the user already typed.
  // LUM-817 — fetch the mailbox signature and seed it into the body while the
  // body is still pristine. Runs on open; a failed fetch is silent, because a
  // missing signature must never block composing.
  useEffect(() => {
    if (!open || !activeSourceId || !api.fetchSignature) return;
    let cancelled = false;
    api
      .fetchSignature(activeSourceId)
      .then((sig) => {
        if (cancelled) return;
        // LUM-830 — this effect can now run more than once per open, because
        // changing the From mailbox changes `activeSourceId`. The append below
        // would then stack the second mailbox's signature under the first.
        //
        // Swapping is safe even once the user has typed, because it substitutes
        // the exact previous block and nothing else. If they EDITED the
        // signature the block no longer matches, and it is left alone — they
        // made it theirs, and replacing it would discard their edit.
        //
        // The decision reads `bodyRef`, not a flag set inside a `setBody`
        // updater: an updater runs on the NEXT render, so a flag assigned in
        // one is still false when the line after it executes. `bodyRef` is
        // assigned on every render and therefore holds the live body here.
        const previous = signatureRef.current;
        const previousBlock = previous ? signatureBlock(previous) : "";
        if (previous && previous !== sig && bodyRef.current.includes(previousBlock)) {
          signatureRef.current = sig;
          setSignature(sig);
          // A REPLACER FUNCTION, not a replacement string: `String.replace`
          // interprets `$&`, `` $` ``, `$'` and `$$` in a replacement string, so
          // a signature quoting a price ("from $5") would mangle itself and
          // splice surrounding body text into the message. A function is passed
          // through verbatim.
          setBody((current) => current.replace(previousBlock, () => signatureBlock(sig)));
          return;
        }
        // An EMPTY new signature is a real setting, not an absent one, and
        // everything below assumes there is something to seed. Returning here
        // AFTER the swap is what clears the previous mailbox's signature —
        // returning before it would send Work's job title on a Personal
        // message, which is a wrong send rather than a wrong render.
        if (!sig) return;
        // Set the REF before the state: `signatureRef.current = signature` runs
        // on render, and the reply-draft callback can fire before React commits
        // that render -- so it would still read "" and judge a body already
        // holding the signature as "not pristine", dropping the draft. Assigning
        // here makes the ref true the moment the value is known.
        signatureRef.current = sig;
        setSignature(sig);
        // Both orderings have to work, and only one of them is obvious.
        // If the signature lands FIRST, the body is empty and takes the block.
        // If the reply DRAFT lands first, the body already holds the draft --
        // seeding "only when empty" would then drop the signature silently,
        // which is what the test for this caught. So append to whatever the
        // untouched body holds, and never twice.
        setBody((current) => {
          if (bodyTouched || current.includes(sig)) return current;
          return current ? `${current}${signatureBlock(sig)}` : signatureBlock(sig);
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // `bodyTouched` is read, not depended on: re-running when the user starts
    // typing would re-seed over what they wrote.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeSourceId, api]);

  useEffect(() => {
    if (!open || mode !== "reply" || !signalId || !api.fetchReplyDraft) return;
    let cancelled = false;
    setDrafting(true);
    api
      .fetchReplyDraft(signalId)
      .then((draft) => {
        if (cancelled) return;
        // Anti-clobber: only fill an untouched, still-empty body.
        // LUM-817: "pristine" now includes a body holding only the seeded
        // signature, and the draft is placed ABOVE it so the signature is not
        // lost when a draft lands.
        setBody((current) =>
          !bodyTouched && isPristine(current, signatureRef.current)
            ? `${draft}${signatureBlock(signatureRef.current)}`
            : current,
        );
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setDrafting(false);
      });
    return () => {
      cancelled = true;
    };
    // Intentionally runs once per open/signal; bodyTouched is read via the setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mode, signalId]);

  // LUM-712: in FORWARD mode, list the original's attachments so the user can
  // choose which ride along. Deliberately not auto-selected — forwarding a 20 MB
  // file someone did not notice is exactly the surprise the approval card exists
  // to prevent, and an unticked box is a cheaper mistake than an unwanted send.
  useEffect(() => {
    if (!open || mode !== "forward" || !signalId || !api.listAttachments) return;
    let cancelled = false;
    api
      .listAttachments(signalId)
      .then((res) => {
        if (cancelled) return;
        setOriginalAttachments(res.available ? res.rows : []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mode, signalId]);

  if (!open) return null;

  // What actually gets SENT: the user's note plus the (possibly trimmed) quoted
  // original. Joins only non-empty parts so a note-less forward has no leading
  // blank line. Used by BOTH send and draft payloads, and passed to the confirm
  // card, so the preview can never drift from the payload.
  const composedBody = (): string =>
    [body.trim() ? body : "", quote.trim() ? quote : ""].filter(Boolean).join("\n\n");

  // Empty attachment_ids are OMITTED for the same reason cc/bcc are: a composer
  // that attached nothing must produce byte-for-byte the payload it did before.
  const attachmentExtras = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    if (staged.length) out.attachment_ids = staged.map((a) => a.id);
    // Only on the forward shapes — the handler rejects it elsewhere, and an
    // empty list must leave the payload byte-for-byte what it was before.
    if (mode === "forward" && forwardPartIndexes.length) {
      out.forward_part_indexes = [...forwardPartIndexes].sort((a, b) => a - b);
    }
    return out;
  };

  // Empty cc/bcc are OMITTED so an unused feature changes no existing payload.
  const recipientExtras = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    const ccList = splitAddrs(cc);
    const bccList = splitAddrs(bcc);
    if (ccList.length) out.cc = ccList;
    if (bccList.length) out.bcc = bccList;
    return out;
  };

  const sendPayload = (): { name: string; payload: Record<string, unknown> } => {
    const extras = { ...recipientExtras(), ...attachmentExtras() };
    if (mode === "reply") {
      return {
        name: "email_reply",
        payload: { signal_id: signalId, body: composedBody(), subject, ...extras },
      };
    }
    if (mode === "forward") {
      return {
        name: "email_forward",
        payload: {
          signal_id: signalId,
          to: splitAddrs(to),
          subject,
          body: composedBody(),
          ...extras,
        },
      };
    }
    return {
      name: "email_compose",
      payload: {
        source_id: activeSourceId,
        to: splitAddrs(to),
        subject,
        body: composedBody(),
        ...extras,
      },
    };
  };

  const draftPayload = (): { name: string; payload: Record<string, unknown> } => {
    const extras = { ...recipientExtras(), ...attachmentExtras() };
    if (mode === "reply") {
      return {
        name: "email_save_draft",
        payload: { signal_id: signalId, body: composedBody(), subject, ...extras },
      };
    }
    if (mode === "forward") {
      // The forward-draft shape (LUM-698): signal_id + non-empty `to`.
      return {
        name: "email_save_draft",
        payload: {
          signal_id: signalId,
          to: splitAddrs(to),
          subject,
          body: composedBody(),
          ...extras,
        },
      };
    }
    return {
      name: "email_save_draft",
      payload: {
        source_id: activeSourceId,
        to: splitAddrs(to),
        subject,
        body: composedBody(),
        ...extras,
      },
    };
  };

  // LUM-682: abandon the review-created send proposal so it doesn't linger as
  // `pending` (the composer creates it at Review; Save-to-Drafts / Back / close all
  // abandon it). Best-effort — never blocks the UI on the cancel. A sent proposal is
  // consumed, never cancelled.
  // LUM-793: the only thing standing between a failed send and a lost message.
  // Extracted so the Escape path and the Cancel button share one prompt, and so a
  // test can drive it. Returns true when the user accepts the discard.
  function confirmDiscardAfterFailure(): boolean {
    return window.confirm(
      "Discard this message? It hasn’t been sent, and closing will lose what you wrote. " +
        "Use Save to Drafts if you want to keep it.",
    );
  }

  function cancelPendingSend() {
    const id = sendProposalId;
    if (id != null && sendStatus !== "sent" && api.cancelProposal) {
      void api.cancelProposal(id).catch(() => {});
    }
    setSendProposalId(null);
  }

  // Refine (LUM-684): rewrite the body via the on-box model, shown as a
  // non-destructive preview. Snapshots the requested body so a transcript/typing
  // that lands mid-flight can't be clobbered by a stale rewrite.
  async function runRefine(req: { preset?: string; instruction?: string }) {
    if (!api.refine || refining || !body.trim()) return;
    const requestedBody = body;
    setRefining(true);
    setRefineError(null);
    try {
      const res = await api.refine({ text: requestedBody, ...req });
      if (!res.available) {
        setRefineUnavailable(true);
        return;
      }
      // Drop a stale result: the user changed the text while refine was in flight
      // (compare the LIVE body via the ref — the closure's `body` is stale here).
      if (bodyRef.current !== requestedBody) return;
      if (res.text && res.text.trim()) {
        setRefinePreview(res.text);
      } else {
        setRefineError("Couldn’t refine — try again.");
      }
    } catch {
      setRefineError("Couldn’t refine — try again.");
    } finally {
      setRefining(false);
    }
  }

  function applyRefine() {
    if (refinePreview == null) return;
    setPreRefineBody(body);
    setBodyTouched(true);
    setBody(refinePreview);
    setRefinePreview(null);
  }

  function undoRefine() {
    if (preRefineBody == null) return;
    setBody(preRefineBody);
    setPreRefineBody(null);
  }

  async function goToConfirm() {
    cancelPendingSend(); // re-Review after Back abandons the previous proposal
    const { name, payload } = sendPayload();
    try {
      const created = await api.createProposal(name, payload);
      setSendProposalId(created.proposal_id);
      setFirstContact(created.first_contact);
      setSendStatus("idle");
      setFailureCode(null);
      setFailureMessage(null);
      setPhase("confirm");
    } catch {
      // Surface as a failed confirm so the text is preserved and Retry is offered.
      setFirstContact(false);
      setSendStatus("failed");
      setFailureMessage("Couldn’t prepare the message.");
      setPhase("confirm");
    }
  }

  async function runProposal(
    build: () => { name: string; payload: Record<string, unknown> },
    reuseId: number | null,
    kind: "sent" | "saved",
  ): Promise<void> {
    setSendStatus("sending");
    setFailureCode(null);
    setFailureMessage(null);
    try {
      let id = reuseId;
      if (id == null) {
        const created = await api.createProposal(build().name, build().payload);
        id = created.proposal_id;
      }
      await api.approveProposal(id);
      const result = await api.executeProposal(id);
      if (result.success) {
        // Save-to-Drafts consumed its own proposal; abandon the review-created send
        // proposal so it doesn't linger as pending (LUM-682).
        if (kind === "saved") cancelPendingSend();
        setSuccessKind(kind);
        setSendStatus("sent");
        window.setTimeout(() => props.onClose(), 900);
      } else {
        setSendStatus("failed");
        setFailureCode(result.code);
        setFailureMessage(result.error);
        setSendProposalId(null); // the row is dead — Retry must build a fresh one
      }
    } catch {
      setSendStatus("failed");
      setFailureMessage("Send failed. Please try again.");
      setSendProposalId(null);
    }
  }

  // Send: on first attempt reuse the review-created proposal; on Retry (previous
  // failure cleared sendProposalId) build a fresh one.
  const onSend = () => void runProposal(sendPayload, sendProposalId, "sent");
  // Save-to-Drafts is always its own fresh proposal, and reports as "saved".
  const onSaveDraft = () => void runProposal(draftPayload, null, "saved");

  return (
    <dialog
      ref={dialogRef}
      className="lumogis-composer"
      aria-label={
        mode === "reply" ? "Reply to email" : mode === "forward" ? "Forward email" : "Compose email"
      }
      onCancel={(e) => {
        // Escape: abandon any pending send proposal, then let the parent close.
        // LUM-793: but NOT silently after a failed send. The composed text lives only
        // in React state — there is no draft autosave — so one reflexive Escape on a
        // failure screen loses the message. It is still in `action_proposals.payload`
        // server-side, but no surface shows it (that is LUM-793's other half), so from
        // the user's side it is simply gone. Confirm first; Save to Drafts and Retry
        // are both one click away on the card behind this dialog.
        if (sendStatus === "failed" && !confirmDiscardAfterFailure()) {
          e.preventDefault();
          return;
        }
        cancelPendingSend();
        props.onClose();
      }}
    >
      {mode === "reply" && replyRecipient ? (
        <header className="lumogis-composer__context">
          In reply to: <strong>{replyRecipient}</strong>
          {originalSubject ? ` — ${originalSubject}` : ""}
        </header>
      ) : null}
      {mode === "forward" ? (
        <header className="lumogis-composer__context">
          Forwarding: <strong>{originalSubject || "this email"}</strong>
          {replyRecipient ? ` — from ${replyRecipient}` : ""}
        </header>
      ) : null}

      {phase === "compose" ? (
        <div className="lumogis-composer__form">
          {/* LUM-830 — which mailbox this leaves from, named before it is sent.
              ADR-213 asks for the choice to be VISIBLE, not merely correct, so
              the row renders with one mailbox too: a plain statement then, a
              real choice when there is one to make. Only compose mode — a reply
              and a forward inherit their mailbox from the message they answer,
              and offering a choice there would invite changing it, which the
              threading headers make wrong. */}
          {mode === "compose" && mailboxes.length > 0 ? (
            <div className="lumogis-composer__from">
              {mailboxes.length === 1 ? (
                <p className="lumogis-composer__from-fixed">
                  From <strong>{mailboxLabel(mailboxes[0])}</strong>
                </p>
              ) : (
                <label className="lumogis-composer__from-label">
                  From
                  <select
                    className="lumogis-composer__from-select"
                    value={activeSourceId ?? ""}
                    onChange={(e) => setChosenSource(e.target.value)}
                  >
                    {mailboxes.map((m) => (
                      <option key={m.id} value={m.id}>
                        {mailboxLabel(m)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {addressesDegraded ? (
                <p className="lumogis-composer__from-degraded" role="status">
                  Couldn't load sending addresses — showing mailbox names.
                </p>
              ) : null}
            </div>
          ) : null}
          {mode !== "reply" ? (
            <RecipientInput
              suggest={api.suggestRecipients ?? _noSuggestions}
              className="lumogis-composer__to"
              label="To"
              placeholder="To"
              value={to}
              onChange={setTo}
            />
          ) : null}
          <input
            className="lumogis-composer__subject"
            aria-label="Subject"
            placeholder="Subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
          {/* LUM-698: collapsed by default, reusing the native <details>
              disclosure convention already used in this folder (the Snooze menu
              on EmailSignalCard and the "Snoozed (N)" section on
              EmailInboxView) rather than inventing a toggle. */}
          {/* LUM-705: `open` is MOUNT-CONSTANT — evaluated from the initial prop,
              never from live `cc` state. A live-bound `open` would re-open the
              disclosure every time the user edits a recipient, so someone who
              collapses it and then fixes a typo watches it spring back. Same
              shape as the quote disclosure below. A pre-filled Cc hidden behind
              a collapsed disclosure is the version of this feature that mails
              four people by surprise, so it opens when there is something in it. */}
          <details
            className="lumogis-composer__recipients"
            data-testid="cc-bcc-disclosure"
            open={(initialCc ?? []).length > 0}
          >
            <summary>Cc / Bcc</summary>
            <RecipientInput
              suggest={api.suggestRecipients ?? _noSuggestions}
              className="lumogis-composer__cc"
              label="Cc"
              placeholder="Cc (comma-separated)"
              value={cc}
              onChange={setCc}
            />
            {/* LUM-818: Bcc autocompletes too (owner decision). The corpus
                EXCLUDES bcc_addrs, so nothing previously blind-copied is ever
                offered back — suggesting into the field leaks nothing that
                suggesting into To does not. */}
            <RecipientInput
              suggest={api.suggestRecipients ?? _noSuggestions}
              className="lumogis-composer__bcc"
              label="Bcc"
              placeholder="Bcc (comma-separated)"
              value={bcc}
              onChange={setBcc}
            />
          </details>
          <textarea
            className="lumogis-composer__body"
            aria-label="Message"
            placeholder={drafting ? "Drafting…" : "Write your message"}
            value={body}
            onChange={(e) => {
              setBodyTouched(true);
              setBody(e.target.value);
            }}
          />
          {/* LUM-698: the quoted original — its OWN editable field so the user can
              SEE and TRIM what they send. Open by default in forward mode (the
              quoted content is the message); collapsed in reply (context, not the
              point), which is also what makes the fetch lazy there. */}
          {mode !== "compose" && api.fetchQuote ? (
            <details
              className="lumogis-composer__quote"
              data-testid="quote-disclosure"
              open={mode === "forward"}
              onToggle={(e) => {
                if ((e.currentTarget as HTMLDetailsElement).open) setQuoteRequested(true);
              }}
            >
              <summary>Quoted original</summary>
              <textarea
                className="lumogis-composer__quote-body"
                aria-label="Quoted original"
                placeholder="The original message will appear here"
                value={quote}
                onChange={(e) => setQuote(e.target.value)}
              />
              {mode === "forward" ? (
                <p className="lumogis-composer__quote-note">
                  Attachments on the original are not forwarded.
                </p>
              ) : null}
            </details>
          ) : null}
          {drafting ? (
            <p className="lumogis-composer__drafting" role="status">
              ✨ Drafting…
            </p>
          ) : body ? (
            <p className="lumogis-composer__provenance">
              ✨ Drafted by Lumogis — edit before sending
            </p>
          ) : null}
          {api.refine ? (
            <div className="lumogis-composer__refine">
              {refinePreview != null ? (
                <div
                  className="lumogis-composer__refine-preview"
                  role="group"
                  aria-label="Refined preview"
                >
                  <p className="lumogis-composer__refine-preview-label">Refined preview</p>
                  <p className="lumogis-composer__refine-preview-body">{refinePreview}</p>
                  <div className="lumogis-composer__refine-preview-actions">
                    <button type="button" onClick={applyRefine}>
                      Apply
                    </button>
                    <button type="button" onClick={() => setRefinePreview(null)}>
                      Discard
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="lumogis-composer__refine-row" role="group" aria-label="Refine">
                    {REFINE_PRESETS.map((p) => (
                      <button
                        key={p.key}
                        type="button"
                        className="lumogis-composer__refine-chip"
                        disabled={refineUnavailable || refining || !body.trim()}
                        title={
                          refineUnavailable ? "Configure a local model to use Refine" : undefined
                        }
                        onClick={() => void runRefine({ preset: p.key })}
                      >
                        {p.label}
                      </button>
                    ))}
                    <input
                      className="lumogis-composer__refine-instruction"
                      aria-label="Custom refine instruction"
                      placeholder="or describe a change…"
                      value={customInstruction}
                      maxLength={400}
                      disabled={refineUnavailable || refining || !body.trim()}
                      onChange={(e) => setCustomInstruction(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && customInstruction.trim()) {
                          e.preventDefault();
                          void runRefine({ instruction: customInstruction.trim() });
                        }
                      }}
                    />
                    {preRefineBody != null ? (
                      <button type="button" onClick={undoRefine}>
                        Undo
                      </button>
                    ) : null}
                  </div>
                  {refining ? (
                    <p className="lumogis-composer__refine-status" role="status">
                      ✨ Refining…
                    </p>
                  ) : null}
                  {refineUnavailable ? (
                    <p className="lumogis-composer__refine-note">
                      Configure a local model to use Refine.
                    </p>
                  ) : null}
                  {refineError ? (
                    <p className="lumogis-composer__refine-note" role="alert">
                      {refineError}
                    </p>
                  ) : null}
                </>
              )}
            </div>
          ) : null}
          {/* LUM-695: staging happens here, so by Review the payload already
              carries references and the approval card can show "3 files, 1.2 MB".
              Hidden entirely when no client is wired, leaving the composer
              exactly as it was. */}
          {/* LUM-712: the original's own attachments, opt-in per file. Shown only
              when the original actually has some, so an ordinary forward is
              visually unchanged. */}
          {mode === "forward" && originalAttachments.length > 0 ? (
            <fieldset
              className="lumogis-composer__forward-attachments"
              data-testid="forward-attachments"
            >
              <legend>Attachments on the original</legend>
              {originalAttachments.map((row) => (
                <label key={row.part_index} className="lumogis-composer__forward-attachment">
                  <input
                    type="checkbox"
                    checked={forwardPartIndexes.includes(row.part_index)}
                    onChange={(e) =>
                      setForwardPartIndexes((current) =>
                        e.target.checked
                          ? [...current, row.part_index]
                          : current.filter((i) => i !== row.part_index),
                      )
                    }
                  />
                  <span>{row.filename}</span>
                  <span className="lumogis-composer__forward-attachment-size">
                    {formatBytes(row.size_bytes)}
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}

          {api.attachmentClient ? (
            <AttachmentPicker
              client={api.attachmentClient as never}
              staged={staged}
              onStaged={(a) => setStaged((current) => [...current, a])}
              onRemoved={(id) => setStaged((current) => current.filter((a) => a.id !== id))}
              disabled={dictating || refining}
            />
          ) : null}
          <div className="lumogis-composer__actions">
            {api.transcribeVoice ? (
              <VoiceDictationButton
                transcribe={api.transcribeVoice}
                onBusyChange={setDictating}
                onTranscript={(text) => {
                  setBodyTouched(true);
                  setBody((current) => (current.length > 0 ? `${current} ${text}` : text));
                }}
              />
            ) : null}
            <button
              type="button"
              className="lumogis-composer__review"
              disabled={
                // LUM-698: a note-less forward is a legitimate send — the
                // forwarded content IS the message — so forward mode enables on
                // body OR quote. `to` is now required for forward as well as
                // compose (previously compose-only).
                (mode === "forward" ? !body.trim() && !quote.trim() : !body.trim()) ||
                (mode !== "reply" && !to.trim()) ||
                dictating ||
                refining
              }
              onClick={() => void goToConfirm()}
            >
              Review
            </button>
            <button
              type="button"
              className="lumogis-composer__cancel"
              onClick={() => {
                // LUM-793: reachable after Back from a failed send, where the text is
                // just as unrecoverable as it is on Escape. Less reflexive than Escape,
                // but the acceptance criterion is "Escape/close", and a confirm the user
                // only ever sees after a real failure is cheap.
                if (sendStatus === "failed" && !confirmDiscardAfterFailure()) return;
                cancelPendingSend();
                props.onClose();
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <SendConfirmCard
          to={mode === "reply" ? (replyRecipient ?? to) : to}
          cc={splitAddrs(cc)}
          bcc={splitAddrs(bcc)}
          mode={mode}
          // LUM-830: name the chosen mailbox rather than falling through to
          // "your address". The host passes no address — `SourceRow` carries
          // none — so this is the mailbox NAME, the same string the settings
          // page and the lane cards use. An explicit `fromAddress` still wins.
          // LUM-831: the non-compose branch used to be a hardcoded `null`, so
          // every reply and forward reached SendConfirmCard's "your address"
          // placeholder no matter what. `chosenMailboxName` cannot serve here —
          // it derives from `mailboxes`, which the reply/forward mounts do not
          // receive — so the host passes the name explicitly as `fromName`.
          // Reply and forward get the SAME `Name — address` treatment compose
          // does. Feeding the address alone here would have made the confirm
          // card read differently depending on which button opened it, for no
          // reason the user could see.
          fromAddress={
            props.fromAddress || props.fromName
              ? mailboxLabel({ name: props.fromName, address: props.fromAddress })
              : (mode === "compose" ? chosenMailboxName || null : null)
          }
          subject={subject}
          // The COMPOSED body — the preview must be what is actually sent.
          body={composedBody()}
          firstContact={firstContact}
          attachmentCount={staged.length + forwardPartIndexes.length}
          attachmentBytes={
            staged.reduce((sum, a) => sum + (a.size_bytes || 0), 0) +
            originalAttachments
              .filter((r) => forwardPartIndexes.includes(r.part_index))
              .reduce((sum, r) => sum + (r.size_bytes || 0), 0)
          }
          status={sendStatus}
          savedDraft={successKind === "saved"}
          failureCode={failureCode}
          failureMessage={failureMessage}
          onSend={onSend}
          onSaveDraft={onSaveDraft}
          onBack={() => {
            cancelPendingSend(); // returning to compose abandons the Ask
            setPhase("compose");
          }}
        />
      )}
    </dialog>
  );
}
