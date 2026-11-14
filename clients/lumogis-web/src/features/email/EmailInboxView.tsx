// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-642 — Email surface v1. A discoverable top-level destination that renders
// classified email signals richly, in three lanes (Needs you / Good to know /
// Done). Email-only scoping uses a household-union query filtered to email
// (GET /signals?source_type=imap) so shared signals from other household members
// still appear — NOT narrowed to the viewer's own mailboxes.
//
// LUM-643 — durable triage state. Two fetches are merged: the recency window
// (`["email","signals","imap"]`) AND the caller's full triaged set
// (`["email","signals","imap","triaged"]`, recency-independent) so Done/Snoozed
// items older than the window are never lost. `effectiveLane` (real state +
// snooze re-surface + default fallback) replaces LUM-642's read-only `deriveLane`.
// Snoozed-not-due items live in a collapsible disclosure, out of the three lanes.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { fetchSendingAddresses } from "../../api/emailSendingAddresses";
import { useAuth } from "../../auth/AuthProvider";
import {
  bulkTriage,
  clearTriage,
  listSignals,
  listSources,
  setTriage,
  MAX_BULK_TRIAGE,
  type EmailSignalRow,
  type SignalsPage,
  type SourceRow,
  type TriageStatus,
} from "../../api/emailSources";
import { postSignalFeedback } from "../../api/relevanceProfile";
import {
  approveProposal,
  cancelProposal,
  createProposal,
  executeProposal,
} from "../../api/approvals";
import {
  fetchQuote,
  fetchReplyAllRecipients,
  fetchReplyDraft,
  refineText,
} from "../../api/emailSend";
import { transcribeVoice } from "../../api/voice";
import { fetchCandidates } from "../../api/emailAttachmentIngest";
import { EmailTabs } from "./EmailTabs";
import { type Lane } from "./lanes";
import { collapseProjections, effectiveLane, SNOOZE_PRESETS } from "./triage";
import { isEmailActivated } from "./activation";
import { fetchRecipientSuggestions } from "../../api/emailRecipients";
import { fetchSignature } from "../../api/emailSignature";
import { hasSmtpCredential, listMyCredentials } from "../../api/connectorCredentials";
import { acknowledgeBounce, fetchBounces } from "../../api/emailBounces";
import { BounceNotice } from "./BounceNotice";
import { listAttachments } from "../../api/emailAttachments";
import { EmailSearchBar } from "./EmailSearchBar";
import { EmailSignalCard } from "./EmailSignalCard";
import { EmailComposer, type ComposerApi } from "./EmailComposer";
import { BulkActionBar } from "./BulkActionBar";
import { ShortcutsHelp } from "./ShortcutsHelp";
import { useTriageKeyboard } from "./useTriageKeyboard";
import { groupForUndo, type TriageSnapshot } from "./undo";
import {
  chunk,
  EMPTY_SELECTION,
  prune,
  recoverCursor,
  selectRange,
  setMany,
  toggle,
  type SelectionState,
} from "./selection";
import {
  EMAIL_TRIAGED_KEY as TRIAGED_KEY,
  EMAIL_WINDOWED_KEY as WINDOWED_KEY,
  useEmailLiveUpdates,
} from "./useEmailLiveUpdates";

/** IMAP locate routes key on the personal original, not the shared projection. */
function locateSignalId(signal: EmailSignalRow): string {
  return signal.published_from ?? signal.signal_id;
}

const LANE_META: ReadonlyArray<{ lane: Lane; label: string; empty: string }> = [
  { lane: "needs_you", label: "Needs you", empty: "Nothing needs you right now." },
  { lane: "good_to_know", label: "Good to know", empty: "Nothing here yet." },
  { lane: "done", label: "Done", empty: "Nothing marked done yet." },
];

/** A triage action: set a status (optionally with a snooze time) or clear it. */
type TriageAction =
  | { signalId: string; kind: "set"; status: TriageStatus; snoozedUntil?: string }
  | { signalId: string; kind: "clear" };

function healthLabel(sources: SourceRow[]): string {
  const imap = sources.filter((s) => s.source_type === "imap");
  if (imap.length === 0) return "";
  const polled = imap
    .map((s) => s.last_polled_at)
    .filter((t): t is string => !!t)
    .sort();
  const oldest = polled[0];
  const anyIdle = imap.some((s) => !s.polling_active);
  const when = oldest ? new Date(oldest).toLocaleString() : "never";
  const count = imap.length === 1 ? "1 mailbox" : `${imap.length} mailboxes`;
  return `${count} · last synced ${when}${anyIdle ? " · a mailbox is paused" : ""}`;
}

/** Apply an optimistic triage change to one cached page (immutably).
 *
 * `removeOnClear` distinguishes the two caches: on the **triaged** fetch a
 * cleared signal no longer has a triage row, so it leaves that set entirely
 * (mirroring the server) — this prevents an old, now-untriaged item (present
 * only in the recency-independent triaged fetch, not the windowed one) from
 * flashing into a lane and then vanishing on refetch. On the **windowed** fetch
 * a clear nulls the status so a still-recent item re-derives into its default
 * lane. (LUM-660) */
function patchPage(
  page: SignalsPage | undefined,
  action: TriageAction,
  removeOnClear: boolean,
): SignalsPage | undefined {
  if (!page) return page;
  if (action.kind === "clear" && removeOnClear) {
    return { ...page, signals: page.signals.filter((s) => s.signal_id !== action.signalId) };
  }
  return {
    ...page,
    signals: page.signals.map((s) =>
      s.signal_id !== action.signalId
        ? s
        : action.kind === "set"
          ? { ...s, triage_status: action.status, snoozed_until: action.snoozedUntil ?? null }
          : { ...s, triage_status: null, snoozed_until: null },
    ),
  };
}

/** Apply an optimistic triage change across every page of the paginated triaged
 *  cache (LUM-659 — the triaged fetch is an infinite query). */
function patchInfinite(
  data: InfiniteData<SignalsPage> | undefined,
  action: TriageAction,
): InfiniteData<SignalsPage> | undefined {
  if (!data) return data;
  return { ...data, pages: data.pages.map((p) => patchPage(p, action, true) as SignalsPage) };
}

/** Bulk siblings of `patchPage` / `patchInfinite` (LUM-696).
 *
 * Build the lookup ONCE and make a single pass. Looping `patchPage` over the
 * action list is O(selected × rows) with a fresh array per action — 200 selected
 * rows over a 200-row page is 40,000 comparisons and 200 allocations on every
 * optimistic update. The single-row helpers above are left untouched so their
 * existing tests keep their meaning. */
function patchPageMany(
  page: SignalsPage | undefined,
  actions: readonly TriageAction[],
  removeOnClear: boolean,
): SignalsPage | undefined {
  if (!page || actions.length === 0) return page;
  const byId = new Map(actions.map((a) => [a.signalId, a]));
  const kept = removeOnClear
    ? page.signals.filter((s) => byId.get(s.signal_id)?.kind !== "clear")
    : page.signals;
  return {
    ...page,
    signals: kept.map((s) => {
      const a = byId.get(s.signal_id);
      if (!a) return s;
      return a.kind === "set"
        ? { ...s, triage_status: a.status, snoozed_until: a.snoozedUntil ?? null }
        : { ...s, triage_status: null, snoozed_until: null };
    }),
  };
}

function patchInfiniteMany(
  data: InfiniteData<SignalsPage> | undefined,
  actions: readonly TriageAction[],
): InfiniteData<SignalsPage> | undefined {
  if (!data || actions.length === 0) return data;
  return {
    ...data,
    pages: data.pages.map((p) => patchPageMany(p, actions, true) as SignalsPage),
  };
}

/** Thrown when a multi-chunk bulk send stops part-way.
 *
 * Carries WHICH ids landed, because the alternative — a plain reject — forces
 * `onError` to revert the whole snapshot, including rows the server has already
 * committed. That would tell the user nothing happened when 200 things did. */
class PartialBulkError extends Error {
  constructor(
    readonly landed: string[],
    readonly failed: string[],
    readonly cause_: unknown,
  ) {
    super("bulk triage partially applied");
  }
}

export function EmailInboxView(): JSX.Element {
  const { client, tokens } = useAuth();
  // LUM-819: a live search replaces the lanes rather than sitting beside them.
  const [searchActive, setSearchActive] = useState(false);
  const queryClient = useQueryClient();
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [text, setText] = useState("");
  // LUM-649: in-the-moment feedback toast (local state, no navigation — follows
  // the ConversationSidebar pattern, not App.tsx's router-state toast).
  const [feedbackToast, setFeedbackToast] = useState<string | null>(null);

  // LUM-672: an informational confirmation should fade on its own (matching
  // App.tsx's 6s auto-dismiss), not linger until the user clicks it. The timer
  // resets whenever the toast text changes and is cleared on unmount.
  useEffect(() => {
    if (feedbackToast === null) return;
    const t = window.setTimeout(() => setFeedbackToast(null), 6000);
    return () => window.clearTimeout(t);
  }, [feedbackToast]);

  // LUM-648: live lane refresh on needs_you / morning_brief SSE events.
  useEmailLiveUpdates(tokens);

  const sourcesQ = useQuery({
    queryKey: ["email", "sources"],
    queryFn: () => listSources(client),
  });
  // LUM-830 — which mailboxes can actually SEND. A mailbox without an SMTP
  // credential cannot, and offering it would reproduce LUM-719: an affordance
  // that fails at the send. Its own query rather than a field on `sources`,
  // because that is where the credential already lives.
  const credentialsQ = useQuery({
    queryKey: ["me", "connector-credentials"],
    queryFn: () => listMyCredentials(client),
  });
  // LUM-831 — the address each sendable mailbox sends FROM. Resolved on demand
  // from the credential (no stored second copy), so it cannot drift from what
  // the send uses.
  //
  // `staleTime` is the load-bearing option, not `retry` (which AuthProvider
  // already defaults to false app-wide). React Query's default `staleTime: 0`
  // plus `refetchOnWindowFocus` would refire this on every alt-tab, and the
  // route's bucket is 20/60s — about twenty focus changes in a minute would
  // self-inflict a 429, which this view renders as "Couldn't load sending
  // addresses" even though nothing is wrong. A mailbox's own address changes
  // only when its credential is re-saved, so five minutes is generous.
  const sendingAddressesQ = useQuery({
    queryKey: ["email", "sending-addresses"],
    queryFn: () => fetchSendingAddresses(client),
    staleTime: 5 * 60 * 1000,
  });
  const signalsQ = useQuery({
    queryKey: WINDOWED_KEY,
    queryFn: () => listSignals(client, { source_type: "imap", limit: 100 }),
  });
  // LUM-742: the tab count only. `limit: 1` because we want `pending_total`, not
  // the page — `total` is the page length AFTER trimming and can never exceed
  // the limit, so reading that would show "1" forever. The route opens no IMAP
  // session, and this never gates first paint: the count is simply absent until
  // it resolves, and the tab renders without one.
  const pendingAttachmentsQ = useQuery({
    queryKey: ["email", "attachment-candidates", "count"],
    queryFn: () => fetchCandidates(client, { limit: 1 }),
  });
  const pendingAttachments = pendingAttachmentsQ.data?.pending_total ?? 0;
  // LUM-659: the triaged set is paginated with a keyset cursor — each page hands
  // back `next_before`, which becomes the next page's `triagedBefore`.
  const triagedQ = useInfiniteQuery({
    queryKey: TRIAGED_KEY,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listSignals(client, { source_type: "imap", triaged: "set", triagedBefore: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage: SignalsPage) => lastPage.next_before ?? undefined,
  });

  // All mutation hooks live ABOVE the early returns so the hook count is constant
  // across renders (Rules of Hooks). Optimistic update on BOTH caches; revert on
  // error; invalidate both keys on settle so the server state reconciles.
  const triageMutation = useMutation({
    mutationFn: async (action: TriageAction): Promise<void> => {
      if (action.kind === "set") {
        await setTriage(client, action.signalId, action.status, action.snoozedUntil);
      } else {
        await clearTriage(client, action.signalId);
      }
    },
    onMutate: async (action: TriageAction) => {
      // One cancel covers both fetches: react-query key matching is prefix-based,
      // so WINDOWED_KEY (["email","signals","imap"]) also matches TRIAGED_KEY
      // (["email","signals","imap","triaged"]). (LUM-660 — was two redundant calls.)
      await queryClient.cancelQueries({ queryKey: WINDOWED_KEY });
      const prevWindowed = queryClient.getQueryData<SignalsPage>(WINDOWED_KEY);
      const prevTriaged = queryClient.getQueryData<InfiniteData<SignalsPage>>(TRIAGED_KEY);
      // getQueryData/setQueryData ARE exact-match, so each cache is patched by its
      // own key (windowed: flat page, null on clear; triaged: paginated, remove on clear).
      queryClient.setQueryData<SignalsPage>(WINDOWED_KEY, (p) => patchPage(p, action, false));
      queryClient.setQueryData<InfiniteData<SignalsPage>>(TRIAGED_KEY, (d) =>
        patchInfinite(d, action),
      );
      return { prevWindowed, prevTriaged };
    },
    onError: (_err, _action, ctx) => {
      if (ctx?.prevWindowed) queryClient.setQueryData(WINDOWED_KEY, ctx.prevWindowed);
      if (ctx?.prevTriaged) queryClient.setQueryData(TRIAGED_KEY, ctx.prevTriaged);
    },
    onSettled: () => {
      // Prefix match invalidates both the windowed and triaged queries in one call.
      void queryClient.invalidateQueries({ queryKey: WINDOWED_KEY });
    },
  });

  // LUM-649: 👍/👎 relevance feedback. Fire-and-forget for the lists (a nudge is
  // soft state — no optimistic cache surgery); on success name the moved term in
  // a toast so the effect is legible in the moment. A durable, browsable view of
  // everything learned lives at /me/relevance.
  const feedbackMutation = useMutation({
    mutationFn: (vars: { signalId: string; positive: boolean }) =>
      postSignalFeedback(client, { itemId: vars.signalId, positive: vars.positive }),
    onSuccess: (res, vars) => {
      const effect = res.effect;
      if (!effect) return;
      const term = vars.positive ? effect.promoted[0] : effect.demoted[0];
      if (!term) return;
      setFeedbackToast(
        vars.positive ? `I'll flag more like this: ${term}` : `I'll flag fewer like this: ${term}`,
      );
    },
    // Feedback is the trust surface of this feature — a silent failure would let
    // the user believe a nudge landed when it didn't. Surface it in the toast.
    onError: () => setFeedbackToast("Couldn't record that — try again."),
  });
  const onFeedback = (signalId: string, positive: boolean) =>
    feedbackMutation.mutate({ signalId, positive });

  const sources = sourcesQ.data?.sources;
  /** The ids of mailboxes this user actually owns.
   *
   * `GET /sources` is `WHERE user_id = %s`, so this is exactly the set the send
   * path's ownership gate will accept. A signal carrying any other `source_id`
   * belongs to another household member's mailbox (LUM-719). */
  const ownedSourceIds = useMemo(() => new Set((sources ?? []).map((s) => s.id)), [sources]);
  const isSendable = useCallback(
    (s: EmailSignalRow) =>
      // No source_id (legacy rows) → unchanged behaviour rather than a silent
      // removal of buttons that work today.
      !s.source_id || ownedSourceIds.has(s.source_id),
    [ownedSourceIds],
  );
  /** LUM-810 — can this user read this message's body?
   *
   * The same ownership question as `isSendable`, deliberately expressed
   * separately: reading needs the mailbox's IMAP credentials and sending needs
   * its SMTP ones, and a future change to one should not silently move the
   * other. `collapseProjections` hands an owner the shared *projection* of
   * their own mail (LUM-661), and that row carries the owner's `source_id`, so
   * this correctly stays true for them — the card then sends
   * `published_from ?? signal_id` so the route resolves the personal original.
   */
  const isReadable = useCallback(
    (s: EmailSignalRow) => !s.source_id || ownedSourceIds.has(s.source_id),
    [ownedSourceIds],
  );

  /** LUM-830 — the mailboxes a composed message could actually leave from.
   *
   * Own IMAP sources that hold an SMTP credential. Both halves matter: a
   * household member's mailbox is not ours to send from, and a mailbox with
   * sending switched off would fail at the send — which is the affordance
   * LUM-719 exists to stop us offering.
   *
   * The NAME, not an address: `SourceRow` carries no address, so this is the
   * same string the settings page and the lane cards use. One name per mailbox
   * rather than a second vocabulary. */
  const sendableMailboxes = useMemo(() => {
    const items = credentialsQ.data?.items;
    if (!items) return [];
    const addrs = sendingAddressesQ.data ?? {};
    return (sources ?? [])
      .filter((s) => s.source_type === "imap" && hasSmtpCredential(items, s.id))
      .map((s) => ({ id: s.id, name: s.name, address: addrs[s.id] }));
  }, [sources, credentialsQ.data, sendingAddressesQ.data]);

  /** LUM-831 — say so when the picker is back to bare labels.
   *
   * Only when there is a choice to get wrong. With one sendable mailbox the
   * user cannot pick the wrong one, so a warning is noise on the common path;
   * with two or more and no addresses, the control is degraded in a way they
   * cannot see. Never blocks the send: `from_addr` is resolved server-side from
   * the credential regardless, so this degrades information, not correctness. */
  const addressesDegraded =
    !sendingAddressesQ.isPending &&
    sendableMailboxes.length >= 2 &&
    sendableMailboxes.every((m) => !m.address);

  /** The mailbox name for a signal, for the confirm card's fallback.
   *
   * Deliberately NOT `mailboxLabelFor`, which suppresses the label below two
   * mailboxes (LUM-821: saying WHICH is noise when there is only one). Here the
   * single-mailbox case is exactly the one that needs a name — "from Work"
   * beats "from this mailbox" — so it shares that helper's data without its
   * count rule. */
  const mailboxNameFor = useCallback(
    (s: EmailSignalRow) => {
      if (!s.source_id || !ownedSourceIds.has(s.source_id)) return undefined;
      return (sources ?? []).find((x) => x.id === s.source_id)?.name;
    },
    [ownedSourceIds, sources],
  );

  /** The resolved sending address for a signal's own mailbox, if we have it. */
  const fromAddressFor = useCallback(
    (s: EmailSignalRow) => {
      if (!s.source_id) return undefined;
      return (sendingAddressesQ.data ?? {})[s.source_id];
    },
    [sendingAddressesQ.data],
  );

  /** True when the user has mailboxes but none of them can send.
   *
   * A distinct state from "no mailboxes", and it needs its own answer: sending
   * is something they can switch on in Settings, so hiding the button silently
   * leaves them with no way to find that out. Deliberately false while the
   * credentials query is still loading or has failed — claiming sending is off
   * for a mailbox whose state we could not read is the mistake `deriveSendState`
   * was written to avoid. */
  const noMailboxCanSend =
    credentialsQ.isSuccess &&
    sendableMailboxes.length === 0 &&
    (sources ?? []).some((s) => s.source_type === "imap");

  /** LUM-821 — how many mailboxes the viewer has, which decides whether saying
   * WHICH one is information or noise. */
  const ownedImapCount = useMemo(
    () => (sources ?? []).filter((s) => s.source_type === "imap").length,
    [sources],
  );

  /** LUM-821 — the mailbox to name on a card, or `""` for none.
   *
   * These lanes are cross-mailbox and always have been: the fetch above passes
   * `source_type: "imap"` with no `source_id`. That is the decided shape —
   * unified read, per-mailbox act — but it leaves a multi-mailbox account
   * reading personal and work mail interleaved with nothing saying which is
   * which, which is precisely how someone replies from the wrong address.
   *
   * Empty in two cases, both deliberate:
   *
   * * **One mailbox.** The answer is never in doubt, so the label would be a
   *   word repeated on every card that tells the user nothing.
   * * **A mailbox the viewer does not own** — a household member's shared mail.
   *   The indicator exists so a reply leaves from the right account, and those
   *   cards offer no reply at all (LUM-719, `isSendable`). Naming the mailbox
   *   there would disclose another member's account naming to answer a question
   *   the card does not pose.
   *
   * `source_label` denormalizes `sources.name` (init.sql:240), so this is the
   * same string the mailbox settings page shows — one name for one mailbox,
   * rather than a second vocabulary to learn. Older rows may carry NULL; those
   * fall back to showing nothing, which is the pre-LUM-821 behaviour.
   */
  const mailboxLabelFor = useCallback(
    (s: EmailSignalRow) => {
      if (ownedImapCount < 2) return "";
      if (!s.source_id || !ownedSourceIds.has(s.source_id)) return "";
      return (s.source_label ?? "").trim();
    },
    [ownedImapCount, ownedSourceIds],
  );

  // Merge the recency window with the full triaged set, deduped by signal_id
  // (a recent triaged signal appears in both fetches). Both rows carry the same
  // per-caller triage_status, so either wins. Then collapse an owner's personal
  // original + its shared projection into one card (LUM-661).
  const triagedSignals = useMemo(
    () => triagedQ.data?.pages.flatMap((p) => p.signals) ?? [],
    [triagedQ.data?.pages],
  );
  const signals = useMemo(() => {
    const byId = new Map<string, EmailSignalRow>();
    for (const s of triagedSignals) byId.set(s.signal_id, s);
    for (const s of signalsQ.data?.signals ?? []) byId.set(s.signal_id, s);
    return collapseProjections([...byId.values()]);
  }, [signalsQ.data?.signals, triagedSignals]);

  const categories = useMemo(() => {
    const seen = new Map<string, string>();
    for (const s of signals) {
      if (s.category && !seen.has(s.category)) seen.set(s.category, s.category_label || s.category);
    }
    return [...seen.entries()].map(([slug, label]) => ({ slug, label }));
  }, [signals]);

  const visible = useMemo(() => {
    const q = text.trim().toLowerCase();
    return signals.filter((s) => {
      if (selectedCategory && s.category !== selectedCategory) return false;
      if (!q) return true;
      const hay =
        `${s.title} ${s.from_display ?? ""} ${s.from_addr ?? ""} ${s.content_summary}`.toLowerCase();
      return hay.includes(q);
    });
  }, [signals, selectedCategory, text]);

  const { byLane, snoozed } = useMemo(() => {
    const groups: Record<Lane, EmailSignalRow[]> = { needs_you: [], good_to_know: [], done: [] };
    const snoozedItems: EmailSignalRow[] = [];
    for (const s of visible) {
      const lane = effectiveLane(s);
      if (lane === "snoozed_hidden") snoozedItems.push(s);
      else groups[lane].push(s);
    }
    return { byLane: groups, snoozed: snoozedItems };
  }, [visible]);

  // A card is "pending" while EITHER its triage OR its feedback mutation is in
  // flight — so the buttons (incl. 👍/👎) disable and a fast double-click can't
  // fire a second feedback request and double-nudge the learned weight.
  const triagePendingId = triageMutation.isPending ? triageMutation.variables?.signalId : undefined;
  const feedbackPendingId = feedbackMutation.isPending
    ? feedbackMutation.variables?.signalId
    : undefined;
  const pendingId = triagePendingId ?? feedbackPendingId;
  const onSetTriage = (signalId: string, status: TriageStatus, snoozedUntil?: string) =>
    triageMutation.mutate({ signalId, kind: "set", status, snoozedUntil });
  const onClearTriage = (signalId: string) => triageMutation.mutate({ signalId, kind: "clear" });

  // LUM-830: the blank composer. Its own flag rather than a signal, because a
  // composed message has no signal behind it — which is exactly what makes the
  // From choice necessary (ADR-213).
  const [composeOpen, setComposeOpen] = useState(false);
  // LUM-652: reply composer. The card supplies the signal; the composer drives the
  // proposal lifecycle through the injected client bindings.
  const [replySignal, setReplySignal] = useState<EmailSignalRow | null>(null);
  const [forwardSignal, setForwardSignal] = useState<EmailSignalRow | null>(null);
  // LUM-705: reply-all. The composer only mounts once the recipients have
  // resolved, so `initialCc` is available at mount — it is mount-constant, and a
  // composer opened before the fetch returned could never receive it.
  const [replyAll, setReplyAll] = useState<{
    signal: EmailSignalRow;
    cc: string[];
    degraded: boolean;
    /** The signal is not in a mailbox this user owns — permanent, not transient. */
    notOwned: boolean;
    truncated: number;
    largeThread: boolean;
  } | null>(null);
  const openReplyAll = useCallback(
    async (signal: EmailSignalRow) => {
      try {
        const r = await fetchReplyAllRecipients(client, locateSignalId(signal));
        setReplyAll({
          signal,
          cc: r.cc,
          degraded: r.degraded,
          notOwned: false,
          truncated: r.truncated,
          largeThread: r.large_thread,
        });
      } catch (err) {
        // A failed lookup is not a failed reply — open a plain Reply and say so.
        // But say WHICH thing failed. A 404 here means the signal is not in a
        // mailbox this user owns (a household-shared email from another member's
        // account), which is permanent: no retry will help, and the send would
        // fail too. Reporting that as "couldn't read the other recipients" —
        // wording that describes a transient mailbox hiccup — would send the user
        // to check their connection over something that can never work.
        const notOwned = err instanceof ApiError && err.status === 404;
        setReplyAll({
          signal,
          cc: [],
          degraded: true,
          notOwned,
          truncated: 0,
          largeThread: false,
        });
      }
    },
    [client],
  );

  // ---------------------------------------------------------------------------
  // LUM-696 — multi-select, bulk actions, keyboard cursor, undo.
  // Every hook below lives ABOVE the three early returns (Rules of Hooks).
  // ---------------------------------------------------------------------------

  const [selection, setSelection] = useState<SelectionState>(EMPTY_SELECTION);
  const [cursorId, setCursorId] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [bulkSnoozeOpen, setBulkSnoozeOpen] = useState(false);
  const [lastAction, setLastAction] = useState<TriageSnapshot[] | null>(null);
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);
  const filterRef = useRef<HTMLInputElement | null>(null);

  /** The flattened rendered order: lanes in render order, then the snoozed
   *  disclosure. Shift-ranges and cursor movement both walk this. */
  const order = useMemo(
    () => [
      ...LANE_META.flatMap(({ lane }) => byLane[lane].map((s) => s.signal_id)),
      ...snoozed.map((s) => s.signal_id),
    ],
    [byLane, snoozed],
  );
  const rowsById = useMemo(() => {
    const m = new Map<string, EmailSignalRow>();
    for (const s of visible) m.set(s.signal_id, s);
    return m;
  }, [visible]);

  // Prune the selection whenever the visible set changes, and recover the cursor
  // when its row leaves. Both are the normal case, not edge cases: a filter
  // keystroke changes `order`, and so does marking the cursor row done.
  const prevOrderRef = useRef<string[]>([]);
  useEffect(() => {
    const previous = prevOrderRef.current;
    prevOrderRef.current = order;
    setSelection((s) => prune(s, order));
    setCursorId((c) => recoverCursor(previous, order, c));
  }, [order]);

  // The keyboard cursor IS DOM focus: focusing the row's checkbox gives us the
  // native focus ring, screen-reader announcement, and no way for the visual
  // cursor to drift from where the browser thinks focus is.
  useEffect(() => {
    if (cursorId === null) return;
    const el = document.querySelector<HTMLElement>(`[data-triage-checkbox="${cursorId}"]`);
    if (el && document.activeElement !== el) el.focus();
  }, [cursorId]);

  const snapshotFor = useCallback(
    (ids: readonly string[]): TriageSnapshot[] =>
      ids.map((id) => {
        const row = rowsById.get(id);
        return {
          signalId: id,
          status: (row?.triage_status as TriageStatus | null) ?? null,
          snoozedUntil: row?.snoozed_until ?? null,
        };
      }),
    [rowsById],
  );

  /** Put the caches back to "only `landed` actually happened".
   *
   * Restore the pre-mutation snapshot, then re-apply exactly the ids the server
   * confirmed, and trim the undo snapshot to match. Shared by the two ways a bulk
   * action can fall short of what was asked: a chunk that threw, and a 200 whose
   * `skipped` list is non-empty. Both must leave the UI describing what the server
   * did, not what the user asked for. */
  const reconcileToLanded = useCallback(
    (
      landed: string[],
      vars: { actions: TriageAction[] },
      ctx?: {
        prevWindowed?: SignalsPage;
        prevTriaged?: InfiniteData<SignalsPage>;
      },
    ) => {
      if (ctx?.prevWindowed) queryClient.setQueryData(WINDOWED_KEY, ctx.prevWindowed);
      if (ctx?.prevTriaged) queryClient.setQueryData(TRIAGED_KEY, ctx.prevTriaged);
      const landedSet = new Set(landed);
      if (landedSet.size > 0) {
        const kept = vars.actions.filter((a) => landedSet.has(a.signalId));
        queryClient.setQueryData<SignalsPage>(WINDOWED_KEY, (p) => patchPageMany(p, kept, false));
        queryClient.setQueryData<InfiniteData<SignalsPage>>(TRIAGED_KEY, (d) =>
          patchInfiniteMany(d, kept),
        );
      }
      // Undo must offer to revert only what actually changed.
      setLastAction((prev) =>
        prev === null ? null : prev.filter((s) => landedSet.has(s.signalId)),
      );
    },
    [queryClient],
  );

  const bulkMutation = useMutation({
    mutationFn: async (vars: {
      actions: TriageAction[];
      status: TriageStatus | null;
      snoozedUntil?: string;
    }): Promise<string[]> => {
      const ids = vars.actions.map((a) => a.signalId);
      const landed: string[] = [];
      for (const batch of chunk(ids, MAX_BULK_TRIAGE)) {
        try {
          const res = await bulkTriage(client, batch, vars.status, vars.snoozedUntil ?? null);
          // Honour the server's `skipped`. A 200 does NOT mean every id in the
          // batch was written: an id the caller can no longer see (concurrently
          // un-shared, or deleted) comes back in `skipped`, not as an error.
          // Treating the whole batch as landed would leave those rows showing an
          // optimistic status the server never stored — until a background
          // refetch quietly corrected them — and would put them in the undo
          // snapshot, so the "N updated" count and Undo would both be wrong.
          const skipped = new Set(res?.skipped ?? []);
          landed.push(...batch.filter((id) => !skipped.has(id)));
          continue;
        } catch (err) {
          // Stop here. Continuing past a failure would make the undo snapshot
          // describe rows that were never changed.
          const landedSet = new Set(landed);
          throw new PartialBulkError(
            landed,
            ids.filter((id) => !landedSet.has(id)),
            err,
          );
        }
      }
      return landed;
    },
    onMutate: async (vars) => {
      await queryClient.cancelQueries({ queryKey: WINDOWED_KEY });
      const prevWindowed = queryClient.getQueryData<SignalsPage>(WINDOWED_KEY);
      const prevTriaged = queryClient.getQueryData<InfiniteData<SignalsPage>>(TRIAGED_KEY);
      queryClient.setQueryData<SignalsPage>(WINDOWED_KEY, (p) =>
        patchPageMany(p, vars.actions, false),
      );
      queryClient.setQueryData<InfiniteData<SignalsPage>>(TRIAGED_KEY, (d) =>
        patchInfiniteMany(d, vars.actions),
      );
      return { prevWindowed, prevTriaged };
    },
    // A 200 with a non-empty `skipped` is NOT a failure, so it never reaches
    // onError — but it still means some rows were optimistically patched that the
    // server did not write. Correct them here, or the lane shows a status the
    // server never stored and Undo offers to revert a change that never happened.
    onSuccess: (landed, vars, ctx) => {
      if (landed.length === vars.actions.length) return;
      reconcileToLanded(landed, vars, ctx);
      const missed = vars.actions.length - landed.length;
      setBulkNotice(
        `Updated ${landed.length} of ${vars.actions.length} — ${missed} no longer available`,
      );
    },
    onError: (err, vars, ctx) => {
      // Restore, THEN re-apply whatever actually landed. A blanket revert would
      // undo rows the server has already committed and claim nothing happened.
      const partial = err instanceof PartialBulkError ? err : null;
      const landed = partial?.landed ?? [];
      reconcileToLanded(landed, vars, ctx);
      // Leave exactly the rows that did NOT land selected, so retry retries the
      // failures rather than re-applying `done` to rows already done.
      const failed = partial?.failed ?? vars.actions.map((a) => a.signalId);
      setSelection({ ids: new Set(failed), anchor: null });
      setBulkNotice(
        landed.length > 0
          ? `Updated ${landed.length} of ${vars.actions.length} — retry the rest`
          : "Couldn't update — retry",
      );
      // `reconcileToLanded` has already trimmed the undo snapshot to exactly the
      // rows the server confirmed, so Undo stays available and correct for those.
      // (This used to null it outright, which threw away a usable undo for the 200
      // rows that DID land just because 50 others failed.)
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: WINDOWED_KEY });
    },
  });

  const runBulk = useCallback(
    (ids: string[], status: TriageStatus | null, snoozedUntil?: string) => {
      if (ids.length === 0) return;
      setLastAction(snapshotFor(ids));
      setBulkNotice(null);
      setBulkSnoozeOpen(false);
      bulkMutation.mutate({
        actions: ids.map((signalId) =>
          status === null
            ? ({ signalId, kind: "clear" } as TriageAction)
            : ({ signalId, kind: "set", status, snoozedUntil } as TriageAction),
        ),
        status,
        snoozedUntil,
      });
    },
    [bulkMutation, snapshotFor],
  );

  /** The rows an action applies to: the selection if there is one, else the
   *  cursor row. This is what makes `e` useful before you have selected
   *  anything. */
  const actionTargets = useCallback((): string[] => {
    if (selection.ids.size > 0) return order.filter((id) => selection.ids.has(id));
    return cursorId ? [cursorId] : [];
  }, [selection.ids, order, cursorId]);

  const undoLast = useCallback(() => {
    if (!lastAction || lastAction.length === 0) return;
    const batches = groupForUndo(lastAction);
    setLastAction(null);
    setBulkNotice(null);
    // One call per distinct prior state — each row returns to ITS OWN state, not
    // to a single state. A naive "undo = clear everything" would silently clear
    // rows that were already Done before the action.
    for (const b of batches) {
      bulkMutation.mutate({
        actions: b.signalIds.map((signalId) =>
          b.status === null
            ? ({ signalId, kind: "clear" } as TriageAction)
            : ({
                signalId,
                kind: "set",
                status: b.status,
                snoozedUntil: b.snoozedUntil ?? undefined,
              } as TriageAction),
        ),
        status: b.status,
        snoozedUntil: b.snoozedUntil ?? undefined,
      });
    }
  }, [lastAction, bulkMutation]);

  const onToggleSelect = useCallback(
    (signalId: string, shiftKey: boolean) => {
      setSelection((s) => (shiftKey ? selectRange(s, order, signalId) : toggle(s, signalId)));
      setCursorId(signalId);
    },
    [order],
  );

  /** Move the cursor to the NEXT row before a single-row keyboard action.
   *
   * Without this, rapid triage does not advance. Marking the cursor row done does
   * not remove it from the surface — the three lanes are all rendered, so the row
   * simply moves to Done and stays in `order`, which means `recoverCursor`
   * correctly keeps the cursor on it. Pressing `e` again then re-marks the SAME
   * row, and the user never walks the list. (React also re-parents the card
   * between lane lists, which drops DOM focus to `<body>`, so the visible cursor
   * disappears too.)
   *
   * Advancing here — computed against the order as it stands BEFORE the mutation
   * — makes `e`-`e`-`e` triage three rows, which is the whole point of a
   * single-key triage flow. Only applies to the cursor path: with a selection,
   * the action is about the selection and the cursor should not move. */
  const advanceCursorPastAction = useCallback(() => {
    if (selection.ids.size > 0 || cursorId === null) return;
    const i = order.indexOf(cursorId);
    if (i === -1) return;
    const next = order[i + 1];
    if (next) setCursorId(next);
  }, [selection.ids, cursorId, order]);

  const keyHandlers = useMemo(
    () => ({
      moveCursor: (delta: 1 | -1) =>
        setCursorId((c) => {
          if (order.length === 0) return null;
          const i = c === null ? -1 : order.indexOf(c);
          const next = Math.min(Math.max(i + delta, 0), order.length - 1);
          return order[i === -1 ? 0 : next];
        }),
      toggleSelect: () => cursorId && setSelection((s) => toggle(s, cursorId)),
      extendSelect: () => cursorId && setSelection((s) => selectRange(s, order, cursorId)),
      clearSelection: () => {
        setSelection(EMPTY_SELECTION);
        setBulkSnoozeOpen(false);
        setHelpOpen(false);
      },
      markDone: () => {
        advanceCursorPastAction();
        runBulk(actionTargets(), "done");
      },
      openSnooze: () => actionTargets().length > 0 && setBulkSnoozeOpen((v) => !v),
      snoozePreset: (i: number) => {
        const preset = SNOOZE_PRESETS[i];
        if (!preset) return;
        advanceCursorPastAction();
        runBulk(actionTargets(), "snoozed", preset.at().toISOString());
      },
      undo: undoLast,
      reply: () => {
        const row = cursorId ? rowsById.get(cursorId) : undefined;
        if (row && (row.from_addr ?? "").trim()) setReplySignal(row);
      },
      forward: () => {
        const row = cursorId ? rowsById.get(cursorId) : undefined;
        if (row) setForwardSignal(row);
      },
      focusFilter: () => filterRef.current?.focus(),
      toggleHelp: () => setHelpOpen((v) => !v),
    }),
    [order, cursorId, rowsById, runBulk, actionTargets, undoLast, advanceCursorPastAction],
  );

  useTriageKeyboard(keyHandlers, {
    // A composer or the help dialog owns the page — do not steal its keystrokes.
    // LUM-830 — `composeOpen` joins this guard: without it the triage shortcuts
    // stay live behind the compose dialog, so `?` opens the shortcuts overlay
    // underneath it and `e`/`s`/`u` triage whatever card the cursor was on.
    // `replyAll` is added in the same breath — it had the identical gap before
    // this change, and leaving one modal unguarded in an expression I am
    // already editing would be a bug kept warm rather than a scope boundary.
    enabled:
      replySignal === null &&
      replyAll === null &&
      forwardSignal === null &&
      !composeOpen &&
      !helpOpen,
    snoozeMenuOpen: bulkSnoozeOpen,
  });
  // Memoised: BounceNotice refetches when `fetchBounces` changes identity, so
  // inline arrows here would re-run the request on every parent render.
  const bounceApi = useMemo(
    () => ({
      list: () => fetchBounces(client),
      acknowledge: (id: string) => acknowledgeBounce(client, id),
    }),
    [client],
  );
  const composerApi: ComposerApi = useMemo(
    () => ({
      createProposal: async (name, payload) => {
        const res = await createProposal(client, name, payload);
        return { proposal_id: res.proposal_id, first_contact: res.first_contact };
      },
      approveProposal: (id) => approveProposal(client, id),
      executeProposal: async (id) => {
        const r = await executeProposal(client, id);
        return { success: r.success, error: r.error, code: r.code };
      },
      cancelProposal: (id) => cancelProposal(client, id),
      // LUM-818 — recipient completion over correspondents the user already has.
      suggestRecipients: (q, signal) => fetchRecipientSuggestions(client, q, signal),
      // LUM-817 — the mailbox's outgoing signature.
      fetchSignature: (sid) => fetchSignature(client, sid),
      fetchReplyDraft: (signalId) => fetchReplyDraft(client, signalId),
      fetchQuote: (signalId, style) => fetchQuote(client, signalId, style),
      transcribeVoice: (blob) => transcribeVoice(client, blob).then((r) => r.text),
      refine: (req) => refineText(client, req),
      // LUM-695: the picker stages through this client directly (multipart, not
      // JSON), so it takes the client rather than a wrapped callback.
      attachmentClient: client,
      // LUM-712: the original's attachment list, so a forward can carry them.
      listAttachments: (signalId) => listAttachments(client, signalId),
    }),
    [client],
  );

  // --- Three distinct route states (never conflate loading with unconfigured) ---
  if (sourcesQ.isLoading || signalsQ.isLoading || triagedQ.isLoading) {
    return (
      <section className="lumogis-email" data-testid="email-inbox-view">
        <h2>Email</h2>
        <p className="lumogis-email__loading" role="status">
          Loading your email…
        </p>
      </section>
    );
  }
  if (sourcesQ.isError || signalsQ.isError || triagedQ.isError) {
    return (
      <section className="lumogis-email" data-testid="email-inbox-view">
        <h2>Email</h2>
        <p role="alert">Couldn't load email.</p>
        <button
          type="button"
          onClick={() => {
            void sourcesQ.refetch();
            void signalsQ.refetch();
            void triagedQ.refetch();
          }}
        >
          Retry
        </button>
      </section>
    );
  }
  if (!isEmailActivated(sources)) {
    return (
      <section className="lumogis-email" data-testid="email-inbox-view">
        <h2>Email</h2>
        <p>No mailbox connected yet.</p>
        <Link to="/me/email-signals">Connect a mailbox in Settings →</Link>
      </section>
    );
  }

  return (
    <section className="lumogis-email" data-testid="email-inbox-view">
      <h2>Email</h2>
      {/* LUM-742: the ONLY change to this file. Everything else about /email —
          the three lanes, filters, bulk triage, shortcuts — is untouched. */}
      <EmailTabs active="triage" pendingCount={pendingAttachments} />
      {/* LUM-830 — the only way to start a message rather than answer one.
          Directly under the tabs because writing is a primary action, not
          something to find. */}
      {sendableMailboxes.length > 0 ? (
        <button
          type="button"
          className="lumogis-email__compose"
          onClick={() => setComposeOpen(true)}
        >
          New message
        </button>
      ) : noMailboxCanSend ? (
        // Not a missing button: sending is something the user can switch on, so
        // silence here is a dead end. Says what is missing and where to fix it.
        <p className="lumogis-email__compose-off" role="status">
          Turn on sending for a mailbox to write new messages.{" "}
          <Link to="/me/email-signals">Mailbox settings →</Link>
        </p>
      ) : null}
      {/* LUM-820 — above the lanes on purpose. A message that did not arrive is
          not triage: the lanes are organised around what to do NEXT, and a
          failed send is something already believed done. */}
      <BounceNotice fetchBounces={bounceApi.list} acknowledge={bounceApi.acknowledge} />
      <p className="lumogis-email__health" role="status">
        {healthLabel(sources ?? [])}
      </p>

      {triageMutation.isError ? (
        <p className="lumogis-email__mutation-error" role="alert">
          Couldn't update — retry
        </p>
      ) : null}

      {feedbackToast !== null ? (
        <p className="lumogis-email__feedback-toast" role="status">
          <button type="button" onClick={() => setFeedbackToast(null)}>
            {feedbackToast}
          </button>
        </p>
      ) : null}

      {categories.length > 0 ? (
        <div className="lumogis-email__filters" role="group" aria-label="Category filters">
          <button
            type="button"
            aria-pressed={selectedCategory === null}
            onClick={() => setSelectedCategory(null)}
          >
            All
          </button>
          {categories.map((c) => (
            <button
              key={c.slug}
              type="button"
              aria-pressed={selectedCategory === c.slug}
              data-category-slug={c.slug}
              onClick={() => setSelectedCategory(c.slug)}
            >
              {c.label}
            </button>
          ))}
        </div>
      ) : null}

      <input
        ref={filterRef}
        type="search"
        className="lumogis-email__search"
        placeholder="Filter loaded email…"
        aria-label="Filter loaded email"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Link className="lumogis-email__search-all" to="/search">
        Search all email →
      </Link>

      <p className="lumogis-email__shortcut-hint">
        Press <kbd>?</kbd> for keyboard shortcuts
      </p>

      {bulkNotice !== null ? (
        <p className="lumogis-email__bulk-notice" role="alert">
          {bulkNotice}
        </p>
      ) : null}

      {/* The undo banner persists until the next action or an explicit dismiss —
          no timer. An undo that vanishes while the user is still deciding is
          worse than a banner that lingers. */}
      {lastAction !== null && lastAction.length > 0 ? (
        <p className="lumogis-email__undo" role="status" data-testid="undo-banner">
          <span>{lastAction.length} updated.</span>
          <button type="button" onClick={undoLast}>
            Undo
          </button>
          <button type="button" aria-label="Dismiss" onClick={() => setLastAction(null)}>
            ×
          </button>
        </p>
      ) : null}

      {/* LUM-819: the bulk bar acts on the LANE selection, so under an active
          search it would offer to act on rows that are no longer on screen. */}
      {searchActive ? null : (
        <BulkActionBar
          count={selection.ids.size}
          busy={bulkMutation.isPending}
          onDone={() => runBulk(actionTargets(), "done")}
          onSnooze={(iso) => runBulk(actionTargets(), "snoozed", iso)}
          onDeselectAll={() => setSelection(EMPTY_SELECTION)}
          snoozeOpen={bulkSnoozeOpen}
          onToggleSnooze={() => setBulkSnoozeOpen((v) => !v)}
        />
      )}

      {/* LUM-819: search mounts above the lanes, and when a search is active it
          REPLACES them. Rendering both would leave the user reading two lists
          with different rules and no cue for which is which. */}
      <EmailSearchBar
        client={client}
        onActiveChange={setSearchActive}
        renderHit={(h) => (
          <EmailSignalCard
            key={h.signal_id}
            signal={h}
            onSetTriage={onSetTriage}
            onClearTriage={onClearTriage}
            onFeedback={onFeedback}
            onReply={setReplySignal}
            onReplyAll={(sig) => void openReplyAll(sig)}
            onForward={setForwardSignal}
            pending={pendingId === h.signal_id}
            attachmentClient={client}
            bodyClient={client}
            sendable={isSendable(h)}
            readable={isReadable(h)}
            mailboxLabel={mailboxLabelFor(h)}
          />
        )}
      />

      {searchActive
        ? null
        : LANE_META.map(({ lane, label, empty }) => {
            const laneIds = byLane[lane].map((s) => s.signal_id);
            const allSelected = laneIds.length > 0 && laneIds.every((id) => selection.ids.has(id));
            return (
              <div key={lane} className="lumogis-email__lane" data-testid={`lane-${lane}`}>
                <h3>
                  {laneIds.length > 0 ? (
                    <input
                      type="checkbox"
                      className="lumogis-email__lane-select-all"
                      data-testid={`select-all-${lane}`}
                      aria-label={`Select all in ${label}`}
                      checked={allSelected}
                      onChange={(e) => setSelection((s) => setMany(s, laneIds, e.target.checked))}
                    />
                  ) : null}
                  {label}
                </h3>
                {byLane[lane].length > 0 ? (
                  <ul className="lumogis-email__list">
                    {byLane[lane].map((s) => (
                      <EmailSignalCard
                        key={s.signal_id}
                        signal={s}
                        onSetTriage={onSetTriage}
                        onClearTriage={onClearTriage}
                        onFeedback={onFeedback}
                        onReply={setReplySignal}
                        onReplyAll={(s) => void openReplyAll(s)}
                        onForward={setForwardSignal}
                        pending={pendingId === s.signal_id}
                        attachmentClient={client}
                        bodyClient={client}
                        sendable={isSendable(s)}
                        readable={isReadable(s)}
                        mailboxLabel={mailboxLabelFor(s)}
                        selected={selection.ids.has(s.signal_id)}
                        onToggleSelect={onToggleSelect}
                      />
                    ))}
                  </ul>
                ) : (
                  <p className="lumogis-email__lane-empty">{empty}</p>
                )}
              </div>
            );
          })}

      {!searchActive && snoozed.length > 0 ? (
        <details className="lumogis-email__snoozed" data-testid="snoozed-disclosure">
          <summary>Snoozed ({snoozed.length})</summary>
          <ul className="lumogis-email__list">
            {snoozed.map((s) => (
              <EmailSignalCard
                key={s.signal_id}
                signal={s}
                onSetTriage={onSetTriage}
                onClearTriage={onClearTriage}
                onFeedback={onFeedback}
                onReply={setReplySignal}
                onReplyAll={(s) => void openReplyAll(s)}
                onForward={setForwardSignal}
                pending={pendingId === s.signal_id}
                attachmentClient={client}
                bodyClient={client}
                sendable={isSendable(s)}
                readable={isReadable(s)}
                mailboxLabel={mailboxLabelFor(s)}
                selected={selection.ids.has(s.signal_id)}
                onToggleSelect={onToggleSelect}
              />
            ))}
          </ul>
        </details>
      ) : null}

      <ShortcutsHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
      {/* The overlay is closed by Escape, which the hook is disabled for while
          it is open — so the dialog owns its own Escape handler. */}

      {triagedQ.hasNextPage ? (
        <button
          type="button"
          className="lumogis-email__show-more"
          disabled={triagedQ.isFetchingNextPage}
          onClick={() => void triagedQ.fetchNextPage()}
        >
          {triagedQ.isFetchingNextPage ? "Loading…" : "Show older done & snoozed"}
        </button>
      ) : null}

      <p className="lumogis-email__setup">
        <Link to="/me/email-signals">Manage mailboxes →</Link>
      </p>

      {/* LUM-830 — the blank composer. Keyed on the open flag for the same
          reason the reply mounts are keyed on their signal: a fresh mount per
          opening, so no body, subject, chosen mailbox or proposal id survives
          from a message the user abandoned. */}
      <EmailComposer
        key={composeOpen ? "compose-open" : "compose-closed"}
        open={composeOpen}
        mode="compose"
        mailboxes={sendableMailboxes}
        addressesDegraded={addressesDegraded}
        api={composerApi}
        onClose={() => setComposeOpen(false)}
      />
      {/* Key by signal so every reply mounts a FRESH composer — no phase/body/
          subject/proposal-id state leaks from a previously-opened email. */}
      <EmailComposer
        key={replySignal?.signal_id ?? "closed"}
        open={replySignal !== null}
        mode="reply"
        signalId={replySignal ? locateSignalId(replySignal) : undefined}
        replyRecipient={replySignal?.from_addr ?? undefined}
        originalSubject={replySignal?.title}
        fromAddress={replySignal ? fromAddressFor(replySignal) : undefined}
        fromName={replySignal ? mailboxNameFor(replySignal) : undefined}
        api={composerApi}
        onClose={() => setReplySignal(null)}
      />
      {/* LUM-705: reply-all is a reply with a pre-filled Cc — same mode, its own
          keyed mount so no state leaks between it and a plain Reply. */}
      {replyAll !== null ? (
        <>
          {replyAll.notOwned ? (
            <p className="lumogis-email__reply-all-notice" role="alert">
              This email is in another household member's mailbox, so Lumogis can't reply from it.
              You can still forward it from one of your own.
            </p>
          ) : replyAll.degraded ? (
            <p className="lumogis-email__reply-all-notice" role="status">
              Couldn't read the other recipients — replying to the sender only.
            </p>
          ) : null}
          {replyAll.truncated > 0 ? (
            <p className="lumogis-email__reply-all-notice" role="alert">
              {replyAll.truncated} further recipients were left out.
            </p>
          ) : null}
          {replyAll.largeThread ? (
            <p className="lumogis-email__reply-all-notice" role="alert">
              This reply goes to {replyAll.cc.length} people. Check the list before sending.
            </p>
          ) : null}
        </>
      ) : null}
      <EmailComposer
        key={replyAll ? `all-${replyAll.signal.signal_id}` : "closed-all"}
        open={replyAll !== null}
        mode="reply"
        signalId={replyAll ? locateSignalId(replyAll.signal) : undefined}
        replyRecipient={replyAll?.signal.from_addr ?? undefined}
        originalSubject={replyAll?.signal.title}
        initialCc={replyAll?.cc}
        fromAddress={replyAll ? fromAddressFor(replyAll.signal) : undefined}
        fromName={replyAll ? mailboxNameFor(replyAll.signal) : undefined}
        api={composerApi}
        onClose={() => setReplyAll(null)}
      />
      {/* Forward gets its own keyed mount for the same reason (LUM-698). */}
      <EmailComposer
        key={forwardSignal ? `fwd-${forwardSignal.signal_id}` : "closed-fwd"}
        open={forwardSignal !== null}
        mode="forward"
        signalId={forwardSignal ? locateSignalId(forwardSignal) : undefined}
        replyRecipient={forwardSignal?.from_addr ?? undefined}
        originalSubject={forwardSignal?.title}
        fromAddress={forwardSignal ? fromAddressFor(forwardSignal) : undefined}
        fromName={forwardSignal ? mailboxNameFor(forwardSignal) : undefined}
        api={composerApi}
        onClose={() => setForwardSignal(null)}
      />
    </section>
  );
}
