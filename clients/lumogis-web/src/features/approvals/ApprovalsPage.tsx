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
//  * Default focus on destructive choice is Cancel.
//  * Hard-limited rows render the action button disabled with tooltip.
/* eslint-disable react-refresh/only-export-components */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { useAuth } from "../../auth/AuthProvider";
import type { ApiClient } from "../../api/client";
import {
  getPendingApprovals,
  setConnectorMode,
  elevateActionType,
  type DeniedActionItem,
  type ElevationCandidateItem,
  type PendingApprovalItem,
} from "../../api/approvals";
import { openReconnectingSse } from "../../api/sse";
import { RiskBadge } from "./RiskBadge";

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

  return (
    <section className="lumogis-approvals" aria-label="Pending approvals">
      <header className="lumogis-approvals__header">
        <h1 className="lumogis-approvals__title">Approvals</h1>
        <button
          type="button"
          className="lumogis-approvals__refresh"
          onClick={invalidate}
          aria-label="Refresh approvals list"
          disabled={loading}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>

      {error && (
        <p className="lumogis-approvals__error" role="alert">
          {error}
        </p>
      )}

      {!loading && items.length === 0 && !error && (
        <p className="lumogis-approvals__empty" role="status">
          No pending approvals. Lumogis is operating normally.
        </p>
      )}

      <ul className="lumogis-approvals__list" role="list" aria-live="polite" aria-busy={loading}>
        {items.map((item) =>
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
          ),
        )}
      </ul>

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

function DeniedActionRow({ item, onSetMode, onElevate }: DeniedActionRowProps): JSX.Element {
  const isHardLimited = item.risk_tier === "hard_limit";
  const canElevate = item.elevation_eligible && !isHardLimited;
  const canSetMode = !isHardLimited;

  return (
    <li className="lumogis-approvals__item lumogis-approvals__item--denied" aria-label={`Denied: ${item.connector} ${item.action_type}`}>
      <div className="lumogis-approvals__item-header">
        <span className="lumogis-approvals__connector">{item.connector}</span>
        <span className="lumogis-approvals__action-type">{item.action_type}</span>
        <RiskBadge tier={item.risk_tier} />
      </div>

      {item.input_summary && (
        <p className="lumogis-approvals__summary">{item.input_summary}</p>
      )}

      <div className="lumogis-approvals__meta">
        <time className="lumogis-approvals__occurred" dateTime={item.occurred_at}>
          {new Date(item.occurred_at).toLocaleString()}
        </time>
      </div>

      <div className="lumogis-approvals__actions">
        {isHardLimited ? (
          <span className="lumogis-approvals__hard-limited" title="This action is hard-limited and cannot be approved.">
            Hard-limited — cannot approve
          </span>
        ) : (
          <>
            {canSetMode && (
              <button
                type="button"
                className="lumogis-approvals__btn lumogis-approvals__btn--primary"
                onClick={() => onSetMode(item.connector, isHardLimited)}
              >
                Switch {item.connector} to DO mode
              </button>
            )}
            {canElevate && (
              <button
                type="button"
                className="lumogis-approvals__btn lumogis-approvals__btn--secondary"
                onClick={() => onElevate(item.connector, item.action_type, isHardLimited)}
              >
                Always allow this action type
              </button>
            )}
          </>
        )}
      </div>
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
    <li className="lumogis-approvals__item lumogis-approvals__item--elevation" aria-label={`Elevation candidate: ${item.connector} ${item.action_type}`}>
      <div className="lumogis-approvals__item-header">
        <span className="lumogis-approvals__connector">{item.connector}</span>
        <span className="lumogis-approvals__action-type">{item.action_type}</span>
        <RiskBadge tier={item.risk_tier} />
      </div>

      <p className="lumogis-approvals__summary">
        Approved <strong>{item.approval_count}</strong> times — eligible for automatic elevation.
      </p>

      <div className="lumogis-approvals__actions">
        {isHardLimited ? (
          <span
            className="lumogis-approvals__hard-limited"
            title="This action type is hard-limited and cannot be elevated to routine."
          >
            Hard-limited — cannot elevate
          </span>
        ) : (
          <button
            type="button"
            className="lumogis-approvals__btn lumogis-approvals__btn--primary"
            onClick={() => onElevate(item.connector, item.action_type, isHardLimited)}
            disabled={!item.elevation_eligible}
            title={!item.elevation_eligible ? "Not eligible for elevation" : undefined}
          >
            Always allow this action type
          </button>
        )}
      </div>
    </li>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────

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
  const cancelRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // Default focus on Cancel (safe default: the destructive choice).
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // Escape closes even when focus is not on the backdrop (Phase 2D).
  useEffect(() => {
    const handleDocKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    document.addEventListener("keydown", handleDocKeyDown);
    return () => document.removeEventListener("keydown", handleDocKeyDown);
  }, []);

  const isSetMode = modal.kind === "set_mode";
  const isHardLimited = modal.isHardLimited;
  const confirmLabel = isSetMode
    ? `Switch ${modal.connector} to DO mode`
    : `Always allow ${modal.kind === "elevate" ? modal.action_type : ""}`;

  return (
    <div
      className="lumogis-approvals__backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="lumogis-approvals__modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approvals-modal-title"
      >
        <h2 id="approvals-modal-title" className="lumogis-approvals__modal-title">
          {isSetMode ? "Switch connector to DO mode?" : "Always allow this action type?"}
        </h2>

        <div className="lumogis-approvals__modal-body">
          {isSetMode ? (
            <p>
              Switching <strong>{modal.connector}</strong> to{" "}
              <strong>DO mode</strong> means Lumogis will execute actions for
              this connector without asking. You can switch back at any time.
            </p>
          ) : (
            modal.kind === "elevate" && (
              <p>
                Marking <strong>{modal.action_type}</strong> on{" "}
                <strong>{modal.connector}</strong> as "always allowed" will auto-approve
                future requests for this action type. Hard-limited actions cannot be
                elevated.
              </p>
            )
          )}

          {actionError && (
            <p className="lumogis-approvals__modal-error" role="alert">
              {actionError}
            </p>
          )}
        </div>

        <div className="lumogis-approvals__modal-footer">
          {/* Cancel is the default focus target per plan §"Pass 1.4 item 14" */}
          <button
            ref={cancelRef}
            type="button"
            className="lumogis-approvals__btn lumogis-approvals__btn--cancel"
            onClick={onClose}
            disabled={actionLoading}
          >
            Cancel
          </button>

          <button
            type="button"
            className="lumogis-approvals__btn lumogis-approvals__btn--confirm"
            onClick={isSetMode ? onSetMode : onElevate}
            disabled={actionLoading || isHardLimited}
            title={isHardLimited ? "Hard-limited — cannot be elevated" : undefined}
          >
            {actionLoading ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Error humanisation ────────────────────────────────────────────────────

function humanizeApprovalError(msg: string): string {
  if (msg.includes("hard_limited_connector"))
    return "This connector is hard-limited and cannot be switched to DO mode.";
  if (msg.includes("hard_limited_action"))
    return "This action type is hard-limited and cannot be elevated.";
  if (msg.includes("unknown_connector"))
    return "Connector not found in the action registry.";
  if (msg.includes("unknown_action"))
    return "Action type not found in the action registry.";
  if (msg.includes("429")) return "Too many approval changes. Try again in a minute.";
  return `Action failed: ${msg}`;
}
