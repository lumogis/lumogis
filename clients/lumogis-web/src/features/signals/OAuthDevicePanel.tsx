// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-687 (674b) — OAuth Device Authorization Grant connect panel. Starts a
// device flow, shows the user a code + verification URL to enter on the
// provider's site, then polls Core until the mailbox is authorized-and-persisted.
//
// The device_code never reaches the browser — only the human-readable user_code
// and the verification URL do. Polling is a self-rescheduling `setTimeout` chain
// (never a fixed-cadence interval): each poll waits the provider-advertised
// `interval` before the next, and a 429 from Core's poll limiter is treated as a
// benign "keep waiting" (never an error). Every async continuation is guarded by
// an alive-ref so a mid-flight unmount can't setState or keep polling.
import { useEffect, useRef, useState } from "react";
import { ApiError, type ApiClient } from "../../api/client";
import {
  pollOAuthDevice,
  startOAuthDevice,
  type ImportScope,
  type OAuthDeviceStartRequest,
  type OAuthDeviceStartResponse,
  type OAuthDevicePollResponse,
} from "../../api/emailSources";

// The network seam — injected in tests so the panel's state machine can be
// driven without a live provider. The defaults are the real API functions.
export interface OAuthDeviceApi {
  start: (
    client: ApiClient,
    provider: string,
    body: OAuthDeviceStartRequest,
  ) => Promise<OAuthDeviceStartResponse>;
  poll: (
    client: ApiClient,
    provider: string,
    flowId: string,
  ) => Promise<OAuthDevicePollResponse>;
}

const DEFAULT_API: OAuthDeviceApi = { start: startOAuthDevice, poll: pollOAuthDevice };

type Phase = "starting" | "waiting" | "connected" | "expired" | "denied" | "error";

export interface OAuthDevicePanelProps {
  client: ApiClient;
  // The registry id used in the route path (e.g. "outlook"), NOT the underlying
  // email_oauth provider key ("microsoft").
  provider: string;
  providerLabel: string;
  email: string;
  mailboxLabel: string;
  // LUM-691 — import-scope parity: forwarded verbatim into the start body.
  importScope?: ImportScope;
  importSince?: string;
  importBefore?: string;
  onConnected: (sourceId: string | null, verified: boolean) => void;
  onCancel?: () => void;
  api?: OAuthDeviceApi;
}

export function OAuthDevicePanel({
  client,
  provider,
  providerLabel,
  email,
  mailboxLabel,
  importScope,
  importSince,
  importBefore,
  onConnected,
  onCancel,
  api = DEFAULT_API,
}: OAuthDevicePanelProps): JSX.Element {
  const [phase, setPhase] = useState<Phase>("starting");
  const [flow, setFlow] = useState<OAuthDeviceStartResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [verified, setVerified] = useState<boolean>(false);

  // Unmount guard + the pending poll timer. Every setState/reschedule checks
  // aliveRef first so a mid-flight unmount is a no-op.
  const aliveRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // React 18 StrictMode double-invokes effects in dev; guard the one-shot start.
  const startedRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    // Kick the flow off exactly once across a StrictMode mount/cleanup/remount;
    // the cleanup below is ALWAYS registered (even on the guarded remount) so a
    // real unmount can never leave the poll timer running.
    const shouldStart = !startedRef.current;
    startedRef.current = true;

    const delayMs = (interval: number) => Math.max(1, interval) * 1000;

    const scheduleNext = (flowId: string, interval: number) => {
      if (!aliveRef.current) return;
      timerRef.current = setTimeout(() => void pollOnce(flowId, interval), delayMs(interval));
    };

    const pollOnce = async (flowId: string, interval: number) => {
      if (!aliveRef.current) return;
      let res: OAuthDevicePollResponse;
      try {
        res = await api.poll(client, provider, flowId);
      } catch (err) {
        // A 429 is Core's poll limiter — a benign "still waiting", never an error.
        // Any other transient failure (network blip, 5xx) is also retried; the
        // flow's own server-side expiry is the real terminal clock, not a fetch
        // hiccup here.
        if (!aliveRef.current) return;
        if (err instanceof ApiError && err.status !== 429 && err.status < 500) {
          setError(err.detail ? String(err.detail) : "Couldn't reach the sign-in service.");
          setPhase("error");
          return;
        }
        scheduleNext(flowId, interval);
        return;
      }
      if (!aliveRef.current) return;

      switch (res.status) {
        case "pending":
          scheduleNext(flowId, interval);
          return;
        case "connected":
          setVerified(!!res.verified);
          setPhase("connected");
          onConnected(res.source_id ?? null, !!res.verified);
          return;
        case "expired":
          setPhase("expired");
          return;
        case "denied":
          setPhase("denied");
          return;
        default:
          setError(res.error ? String(res.error) : "Sign-in failed. Please try again.");
          setPhase("error");
          return;
      }
    };

    const begin = async () => {
      try {
        const started = await api.start(client, provider, {
          email,
          mailbox_label: mailboxLabel,
          ...(importScope ? { import_scope: importScope } : {}),
          ...(importSince ? { import_since: importSince } : {}),
          ...(importBefore ? { import_before: importBefore } : {}),
        });
        if (!aliveRef.current) return;
        setFlow(started);
        setPhase("waiting");
        scheduleNext(started.flow_id, started.interval);
      } catch (err) {
        if (!aliveRef.current) return;
        const detail =
          err instanceof ApiError
            ? err.status === 503
              ? `Connecting ${providerLabel} isn't set up on this server yet.`
              : String(err.detail)
            : "Couldn't start sign-in. Please try again.";
        setError(detail);
        setPhase("error");
      }
    };

    if (shouldStart) void begin();

    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // Start-once on mount; the flow inputs are captured at that point and a
    // re-run would orphan the in-flight device flow. Intentionally empty deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase === "connected") {
    return (
      <div className="lumogis-oauth-device" role="status">
        <p>
          <strong>{email}</strong> is connected.
          {verified
            ? " Your mailbox is verified."
            : " We'll verify it on the first sync."}
        </p>
      </div>
    );
  }

  if (phase === "expired" || phase === "denied" || phase === "error") {
    const message =
      phase === "expired"
        ? "The sign-in code expired before it was approved."
        : phase === "denied"
          ? "Sign-in was declined."
          : (error ?? "Sign-in failed.");
    return (
      <div className="lumogis-oauth-device">
        <p role="alert" className="lumogis-form-error">
          {message}
        </p>
        {onCancel ? (
          <button type="button" onClick={onCancel}>
            Try again
          </button>
        ) : null}
      </div>
    );
  }

  // starting | waiting
  return (
    <div className="lumogis-oauth-device" style={{ display: "grid", gap: "0.5rem" }}>
      <h3>Connect {providerLabel}</h3>
      {phase === "starting" || !flow ? (
        <p role="status">Starting sign-in…</p>
      ) : (
        <>
          <p>
            To connect <strong>{email}</strong>, open the sign-in page and enter this code:
          </p>
          <p aria-label="Device code" style={{ fontSize: "1.5rem", fontWeight: 700, letterSpacing: "0.1em" }}>
            {flow.user_code}
          </p>
          <a
            href={flow.verification_uri_complete || flow.verification_uri}
            target="_blank"
            rel="noreferrer noopener"
          >
            Open the {providerLabel} sign-in page
          </a>
          <p role="status">Waiting for you to approve sign-in…</p>
          <p>Lumogis reads message headers only, and never sends anything without asking.</p>
        </>
      )}
      {onCancel ? (
        <button type="button" onClick={onCancel} style={{ justifySelf: "start" }}>
          Cancel
        </button>
      ) : null}
    </div>
  );
}
