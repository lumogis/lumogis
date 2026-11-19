// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Approvals queue surface — parent plan §"Phase 1 Pass 1.4 items 11–14".
//
// Responsibilities:
//  * Polls GET /api/v1/approvals/pending on mount and after each action.
//  * Subscribes to /api/v1/events (reconnecting SSE) and invalidates the
//    pending list when action_executed or routine_elevation_ready arrives.
//  * Renders two item kinds from the discriminated union:
//    - DeniedActionItem  (kind = "denied_action")
//    - ElevationCandidateItem (kind = "elevation_candidate")
//  * Two modal flows:
//    (a) "Switch to DO mode" → POST /api/v1/approvals/connector/{c}/mode
//    (b) "Always allow this action type" → POST /api/v1/approvals/elevate
//  * Default focus on the confirm dialogs is Cancel (initialFocus).
//  * Hard-limited rows render the action button disabled with tooltip.
/* eslint-disable react-refresh/only-export-components */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { refreshShellState } from "../../api/meShell";
import { useAuth } from "../../auth/AuthProvider";
import type { ApiClient } from "../../api/client";
import {
  getPendingApprovals,
  listPendingProposals,
  setConnectorMode,
  elevateActionType,
  type DeniedActionItem,
  type ElevationCandidateItem,
  type PendingApprovalItem,
  type PendingProposalItem,
} from "../../api/approvals";
import { openReconnectingSse } from "../../api/sse";
import { PageHeader } from "@/components/page/PageHeader";
import { ErrorState } from "@/components/page/ErrorState";
import { LoadingPlaceholder, SkeletonText } from "@/components/page/LoadingState";
import { PageSection } from "@/components/page/PageSection";
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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ChevronDown } from "lucide-react";
import { formatDateTime } from "../../util/formatTimestamp";
import { RiskBadge } from "./RiskBadge";
import { RecoverableSends } from "./RecoverableSends";

// ── Fetch hook ────────────────────────────────────────────────────────────

export interface ApprovalsState {
  items: PendingApprovalItem[];
  loading: boolean;
  error: string | null;
}

export function useApprovals(
  client: ApiClient,
  refreshCount: number,
): ApprovalsState {
  const [items, setItems] = useState<PendingApprovalItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setLoading(true);
    setError(null);

    getPendingApprovals(client, 50, ctrl.signal)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setItems(res.pending);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Failed to load pending approvals: ${msg}`);
        setLoading(false);
      });

    return () => {
      ctrl.abort();
    };
  }, [client, refreshCount]);

  return { items, loading, error };
}

// ── SSE-driven invalidation ───────────────────────────────────────────────

export function useApprovalsInvalidation(
  tokens: ReturnType<typeof useAuth>["tokens"],
  onInvalidate: () => void,
): void {
  const onInvalidateRef = useRef(onInvalidate);
  useEffect(() => {
    onInvalidateRef.current = onInvalidate;
  });

  useEffect(() => {
    const handle = openReconnectingSse({
      url: "/api/v1/events",
      tokens,
      onMessage(msg) {
        if (
          msg.event === "action_executed" ||
          msg.event === "routine_elevation_ready"
        ) {
          onInvalidateRef.current();
        }
      },
    });
    return () => handle.close();
  }, [tokens]);
}

const VISIBLE_ITEMS = 5;

// ── Modal types ───────────────────────────────────────────────────────────

type ModalState =
  | null
  | { kind: "set_mode"; connector: string; isHardLimited: boolean }
  | { kind: "elevate"; connector: string; action_type: string; isHardLimited: boolean };

// ── Main component ────────────────────────────────────────────────────────

export function ApprovalsPage(): JSX.Element {
  const { client, tokens } = useAuth();
  const [refreshCount, setRefreshCount] = useState(0);
  const [modal, setModal] = useState<ModalState>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  /** Element to restore focus when confirmation dialog closes (Phase 2D a11y). */
  const modalReturnFocusRef = useRef<HTMLElement | null>(null);

  const invalidate = useCallback(() => {
    setRefreshCount((n) => n + 1);
    // Keep the sidebar's Approvals count in step (LUM-993).
    refreshShellState();
  }, []);

  useApprovalsInvalidation(tokens, invalidate);

  const { items, loading, error } = useApprovals(client, refreshCount);

  const openSetModeModal = useCallback(
    (connector: string, isHardLimited: boolean) => {
      modalReturnFocusRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setModal({ kind: "set_mode", connector, isHardLimited });
      setActionError(null);
    },
    [],
  );

  const openElevateModal = useCallback(
    (connector: string, action_type: string, isHardLimited: boolean) => {
      modalReturnFocusRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setModal({ kind: "elevate", connector, action_type, isHardLimited });
      setActionError(null);
    },
    [],
  );

  const closeModal = useCallback(() => {
    setModal(null);
    setActionError(null);
    const el = modalReturnFocusRef.current;
    modalReturnFocusRef.current = null;
    queueMicrotask(() => {
      if (el?.isConnected) el.focus();
    });
  }, []);

  const handleSetMode = useCallback(async () => {
    if (!modal || modal.kind !== "set_mode") return;
    setActionLoading(true);
    setActionError(null);
    try {
      await setConnectorMode(client, modal.connector, "DO");
      closeModal();
      invalidate();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionError(humanizeApprovalError(msg));
    } finally {
      setActionLoading(false);
    }
  }, [client, modal, closeModal, invalidate]);

  const handleElevate = useCallback(async () => {
    if (!modal || modal.kind !== "elevate") return;
    setActionLoading(true);
    setActionError(null);
    try {
      await elevateActionType(client, modal.connector, modal.action_type);
      closeModal();
      invalidate();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionError(humanizeApprovalError(msg));
    } finally {
      setActionLoading(false);
    }
  }, [client, modal, closeModal, invalidate]);

  // LUM-993 Pass 7: a long list of denied actions is history, not a to-do. The first few
  // stay open; the rest sit in a collapsed section so the page does not become a wall.
  const visible = items.slice(0, VISIBLE_ITEMS);
  const older = items.slice(VISIBLE_ITEMS);
  const renderItem = (item: PendingApprovalItem): JSX.Element =>
    item.kind === "denied_action" ? (
      <DeniedActionRow
        key={`denied-${item.action_log_id}`}
        item={item}
        onSetMode={openSetModeModal}
        onElevate={openElevateModal}
      />
    ) : (
      <ElevationCandidateRow
        key={`elev-${item.connector}-${item.action_type}`}
        item={item}
        onElevate={openElevateModal}
      />
    );

  return (
    <section className="mx-auto w-full max-w-3xl p-4" aria-label="Pending approvals">
      <PageHeader
        title="Approvals"
        description="Things Lumogis stopped to ask about, and actions you have approved often enough to allow for good."
        action={
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={invalidate}
            aria-label="Refresh approvals list"
            disabled={loading}
          >
            {loading ? "Loading…" : "Refresh"}
          </Button>
        }
      />

      {error && <ErrorState message={error} onRetry={invalidate} doctorHint={false} />}

      <PendingSendProposals client={client} />

      {/* LUM-793: failed sends. Distinct from the section above — those are
          proposals the user abandoned, these are ones that were sent and did
          not arrive. */}
      <RecoverableSends client={client} />

      {loading && items.length === 0 && !error && (
        <LoadingPlaceholder label="Loading approvals">
          <SkeletonText lines={3} />
        </LoadingPlaceholder>
      )}

      {!loading && items.length === 0 && !error && (
        <p className="m-0 py-8 text-center text-sm text-muted-foreground" role="status">
          No pending approvals. Lumogis is operating normally.
        </p>
      )}

      <ul className="m-0 flex list-none flex-col gap-3 p-0" role="list" aria-live="polite" aria-busy={loading}>
        {visible.map(renderItem)}
      </ul>

      {older.length > 0 && (
        <Collapsible className="mt-3 border-t pt-2">
          <CollapsibleTrigger
            render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
          >
            <ChevronDown
              aria-hidden="true"
              className="transition-transform group-aria-expanded/trigger:rotate-180"
            />
            {`Show ${older.length} older ${older.length === 1 ? "item" : "items"}`}
          </CollapsibleTrigger>
          <CollapsibleContent keepMounted className="pt-2">
            <ul className="m-0 flex list-none flex-col gap-3 p-0" role="list">
              {older.map(renderItem)}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}

      {modal && (
        <ApprovalsModal
          modal={modal}
          actionLoading={actionLoading}
          actionError={actionError}
          onClose={closeModal}
          onSetMode={handleSetMode}
          onElevate={handleElevate}
        />
      )}
    </section>
  );
}

// ── Row components ────────────────────────────────────────────────────────

interface DeniedActionRowProps {
  item: DeniedActionItem;
  onSetMode: (connector: string, isHardLimited: boolean) => void;
  onElevate: (connector: string, action_type: string, isHardLimited: boolean) => void;
}

function ItemHeader({
  connector,
  actionType,
  tier,
}: {
  connector: string;
  actionType: string;
  tier: DeniedActionItem["risk_tier"];
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-medium">{connector}</span>
      <span className="text-muted-foreground">{actionType}</span>
      <RiskBadge tier={tier} />
    </div>
  );
}

function DeniedActionRow({ item, onSetMode, onElevate }: DeniedActionRowProps): JSX.Element {
  const isHardLimited = item.risk_tier === "hard_limit";
  const canElevate = item.elevation_eligible && !isHardLimited;
  // The server decides which rows can be switched (`suggested_action`, LUM-962):
  // do not re-derive connector rules in the client.
  const canSetMode = !isHardLimited && item.suggested_action !== "explain_only";

  return (
    <li aria-label={`Denied: ${item.connector} ${item.action_type}`}>
      <Card size="sm">
        <CardContent className="flex flex-col gap-2">
          <ItemHeader connector={item.connector} actionType={item.action_type} tier={item.risk_tier} />

          {item.input_summary && <p className="m-0 text-sm">{item.input_summary}</p>}

          <time className="text-xs text-muted-foreground" dateTime={item.occurred_at}>
            {formatDateTime(item.occurred_at)}
          </time>

          <div className="flex flex-wrap items-center gap-2">
            {isHardLimited ? (
              <span
                className="text-sm text-muted-foreground"
                title="This action is hard-limited and cannot be approved."
              >
                Hard-limited — cannot approve
              </span>
            ) : (
              <>
                {canSetMode && (
                  <Button type="button" size="sm" onClick={() => onSetMode(item.connector, isHardLimited)}>
                    Switch {item.connector} to DO mode
                  </Button>
                )}
                {canElevate && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => onElevate(item.connector, item.action_type, isHardLimited)}
                  >
                    Always allow this action type
                  </Button>
                )}
                {!canSetMode && !canElevate && (
                  <span className="text-sm text-muted-foreground">This can&apos;t be switched from here.</span>
                )}
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </li>
  );
}

interface ElevationCandidateRowProps {
  item: ElevationCandidateItem;
  onElevate: (connector: string, action_type: string, isHardLimited: boolean) => void;
}

function ElevationCandidateRow({ item, onElevate }: ElevationCandidateRowProps): JSX.Element {
  const isHardLimited = item.risk_tier === "hard_limit";

  return (
    <li aria-label={`Elevation candidate: ${item.connector} ${item.action_type}`}>
      <Card size="sm">
        <CardContent className="flex flex-col gap-2">
          <ItemHeader connector={item.connector} actionType={item.action_type} tier={item.risk_tier} />

          <p className="m-0 text-sm">
            Approved <strong>{item.approval_count}</strong> times — eligible for automatic elevation.
          </p>

          <div className="flex flex-wrap items-center gap-2">
            {isHardLimited ? (
              <span
                className="text-sm text-muted-foreground"
                title="This action type is hard-limited and cannot be elevated to routine."
              >
                Hard-limited — cannot elevate
              </span>
            ) : (
              <Button
                type="button"
                size="sm"
                onClick={() => onElevate(item.connector, item.action_type, isHardLimited)}
                disabled={!item.elevation_eligible}
                title={!item.elevation_eligible ? "Not eligible for elevation" : undefined}
              >
                Always allow this action type
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </li>
  );
}

// ── Confirm dialog ────────────────────────────────────────────────────────

interface ApprovalsModalProps {
  modal: NonNullable<ModalState>;
  actionLoading: boolean;
  actionError: string | null;
  onClose: () => void;
  onSetMode: () => void;
  onElevate: () => void;
}

function ApprovalsModal({
  modal,
  actionLoading,
  actionError,
  onClose,
  onSetMode,
  onElevate,
}: ApprovalsModalProps): JSX.Element {
  const isSetMode = modal.kind === "set_mode";
  const isHardLimited = modal.isHardLimited;
  const confirmLabel = isSetMode
    ? `Switch ${modal.connector} to DO mode`
    : `Always allow ${modal.kind === "elevate" ? modal.action_type : ""}`;

  // LUM-993 Pass 7: was a hand-made dialog element. The ui AlertDialog traps focus, closes on
  // Escape and hands focus back to the invoking button. Cancel is the first button in the
  // footer, so it takes the initial focus (the safe default for a consequential choice).
  return (
    <AlertDialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <AlertDialogContent
        initialFocus={() => document.querySelector<HTMLElement>('[data-slot="alert-dialog-cancel"]')}
      >
        <AlertDialogHeader>
          <AlertDialogTitle data-testid="approvals-modal-title">
            {isSetMode ? "Switch connector to DO mode?" : "Always allow this action type?"}
          </AlertDialogTitle>
          <AlertDialogDescription render={<div />}>
            {isSetMode ? (
              <p className="m-0">
                Switching <strong>{modal.connector}</strong> to <strong>DO mode</strong> means Lumogis
                will execute actions for this connector without asking. You can switch back at any
                time.
              </p>
            ) : (
              modal.kind === "elevate" && (
                <p className="m-0">
                  Marking <strong>{modal.action_type}</strong> on <strong>{modal.connector}</strong> as
                  "always allowed" will auto-approve future requests for this action type.
                  Hard-limited actions cannot be elevated.
                </p>
              )
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {actionError && (
          <Alert variant="destructive">
            <AlertDescription>{actionError}</AlertDescription>
          </Alert>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel disabled={actionLoading}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={isSetMode ? onSetMode : onElevate}
            disabled={actionLoading || isHardLimited}
            title={isHardLimited ? "Hard-limited — cannot be elevated" : undefined}
          >
            {actionLoading ? "Working…" : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ── Error humanisation ────────────────────────────────────────────────────

function humanizeApprovalError(msg: string): string {
  if (msg.includes("hard_limited_connector"))
    return "This connector is hard-limited and cannot be switched to DO mode.";
  if (msg.includes("hard_limited_action"))
    return "This action type is hard-limited and cannot be elevated.";
  if (msg.includes("unknown_connector"))
    return "This connector can't be switched to DO mode right now.";
  if (msg.includes("unknown_action"))
    return "This action type can't be elevated right now.";
  if (msg.includes("429")) return "Too many approval changes. Try again in a minute.";
  return `Action failed: ${msg}`;
}

// ── LUM-652: send/save-draft proposal recovery listing ─────────────────────
// The primary send-confirm path is the composer's SendConfirmCard. This is a
// safety net: a send proposal the user created then abandoned (chose Save, or
// closed the card) stays `pending` and would otherwise be invisible. It is
// read-only here — surfacing, not re-driving, the abandoned proposal.
const _SEND_ACTION_NAMES = new Set([
  "email_reply",
  "email_compose",
  "email_forward",
  "email_save_draft",
  "email_unsubscribe",
]);

function _actionLabel(actionName: string): string {
  if (actionName === "email_save_draft") return "Draft";
  if (actionName === "email_unsubscribe") return "Unsubscribe";
  if (actionName === "email_forward") return "Forward";
  return "Reply/compose";
}

interface UnsubscribeTarget {
  from_addr?: string;
  mailto_addr?: string;
}

/** The sender list + resolved recipients of a pending unsubscribe batch. */
function UnsubscribeTargets({ payload }: { payload?: Record<string, unknown> }): JSX.Element | null {
  const targets = (payload?.targets as UnsubscribeTarget[] | undefined) ?? [];
  if (targets.length === 0) return null;
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm" data-testid="unsubscribe-targets">
      {targets.map((t, i) => (
        <li key={`${t.from_addr ?? i}`}>
          <span>{t.from_addr}</span>
          {" → "}
          <span className="text-muted-foreground">{t.mailto_addr}</span>
        </li>
      ))}
    </ul>
  );
}

export function PendingSendProposals({ client }: { client: ApiClient }): JSX.Element | null {
  const [items, setItems] = useState<PendingProposalItem[]>([]);

  useEffect(() => {
    const ctrl = new AbortController();
    listPendingProposals(client, ctrl.signal)
      .then((res) => setItems(res.proposals.filter((p) => _SEND_ACTION_NAMES.has(p.action_name))))
      .catch(() => {
        /* recovery listing is best-effort; never block the page */
      });
    return () => ctrl.abort();
  }, [client]);

  if (items.length === 0) return null;

  return (
    <PageSection title="Unsent messages">
      <ul className="m-0 flex list-none flex-col gap-3 p-0" role="list">
        {items.map((p) => (
          <li key={p.proposal_id} className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{_actionLabel(p.action_name)}</span>
            {/* LUM-697: an unsubscribe batch is approved as ONE proposal, so the
                approver must be able to see every sender AND the exact address
                each will be mailed. The recipient was resolved server-side at
                propose time and stored in the payload, which is returned
                verbatim — so this reads what will actually be sent, not a
                client-side reconstruction of it. */}
            {p.action_name === "email_unsubscribe" ? (
              <UnsubscribeTargets payload={p.payload} />
            ) : null}
            {p.first_contact ? (
              <Badge variant="outline" role="note">
                first contact
              </Badge>
            ) : null}
            <span className="text-muted-foreground">{p.status}</span>
          </li>
        ))}
      </ul>
    </PageSection>
  );
}
