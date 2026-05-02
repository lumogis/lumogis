// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Read-only notification status — GET /api/v1/me/notifications.
// Phase 4C: enrol + manage browser Web Push in PushOptIn.
// Edit ntfy credentials under Settings → Connectors.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import { fetchMeNotifications } from "../../api/meNotifications";
import { useAuth } from "../../auth/AuthProvider";
import { PushOptIn } from "./PushOptIn";

const TIER_LABELS: Record<string, string> = {
  user: "Your account",
  household: "Household shared",
  system: "Instance / system",
  env: "Environment fallback",
  none: "None",
};

function labelTier(raw: string): string {
  return TIER_LABELS[raw] ?? raw.replace(/_/g, " ");
}

function triState(v: boolean | null | undefined): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  return "Unknown (encrypted)";
}

export function MeNotificationsView(): JSX.Element {
  const { client } = useAuth();
  const [filter, setFilter] = useState<string>("all");

  const q = useQuery({
    queryKey: ["me", "notifications"],
    queryFn: () => fetchMeNotifications(client),
  });

  const filtered = useMemo(() => {
    const channels = q.data?.channels ?? [];
    if (filter === "all") return channels;
    if (filter === "configured") return channels.filter((c) => c.configured);
    if (filter === "not_configured") return channels.filter((c) => !c.configured);
    return channels.filter((c) => c.active_tier === filter);
  }, [q.data?.channels, filter]);

  if (q.isPending) {
    return (
      <section aria-busy="true">
        <h2>Notifications</h2>
        <p>Loading…</p>
      </section>
    );
  }

  if (q.isError) {
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Your session expired or you are not signed in. Refresh the page or sign in again."
          : err.detail
        : "Something went wrong loading notification status.";
    return (
      <section>
        <h2>Notifications</h2>
        <p role="alert">{detail}</p>
      </section>
    );
  }

  const { summary } = q.data!;

  return (
    <section>
      <h2>Notifications</h2>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Read-only overview of notification channels plus browser Web Push enrolment for this tab.{" "}
        <strong>ntfy</strong> credentials are edited under{" "}
        <a href="/me/connectors">Connectors</a>. This page does not send test pushes or reveal tokens outside the
        redacted push surface.
      </p>

      <PushOptIn />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(9rem, 1fr))",
          gap: "0.75rem",
          margin: "1rem 0",
        }}
      >
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Channels</div>
          <div style={{ fontSize: "1.35rem", fontWeight: 700 }} aria-label={`Total channels: ${summary.total}`}>
            {summary.total}
          </div>
        </div>
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Configured</div>
          <div
            style={{ fontSize: "1.35rem", fontWeight: 700 }}
            aria-label={`Configured channels: ${summary.configured}`}
          >
            {summary.configured}
          </div>
        </div>
        <div style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 8, padding: "0.75rem" }}>
          <div style={{ fontSize: "0.8rem", opacity: 0.8 }}>Not configured</div>
          <div
            style={{ fontSize: "1.35rem", fontWeight: 700 }}
            aria-label={`Not configured: ${summary.not_configured}`}
          >
            {summary.not_configured}
          </div>
        </div>
      </div>

      <div style={{ marginBottom: "0.75rem" }}>
        <strong style={{ fontSize: "0.9rem" }}>By active tier</strong>
        <ul style={{ margin: "0.35rem 0 0", paddingLeft: "1.2rem" }}>
          {Object.entries(summary.by_active_tier)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([k, n]) => (
              <li key={k}>
                {labelTier(k)}: {n}
              </li>
            ))}
        </ul>
      </div>

      <div style={{ marginBottom: "1rem" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.25rem", fontSize: "0.9rem" }}>
          Filter
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter channels">
            <option value="all">All</option>
            <option value="configured">Configured only</option>
            <option value="not_configured">Not configured</option>
            <option value="user">Active tier: your account</option>
            <option value="household">Active tier: household</option>
            <option value="system">Active tier: system</option>
            <option value="env">Active tier: env fallback</option>
            <option value="none">Active tier: none</option>
          </select>
        </label>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.9rem" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid rgba(128,128,128,0.35)" }}>
              <th style={{ padding: "0.5rem 0.35rem" }}>Channel</th>
              <th style={{ padding: "0.5rem 0.35rem" }}>Id</th>
              <th style={{ padding: "0.5rem 0.35rem" }}>Status</th>
              <th style={{ padding: "0.5rem 0.35rem" }}>Tier</th>
              <th style={{ padding: "0.5rem 0.35rem" }}>Details</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c) => (
              <tr key={c.connector} style={{ verticalAlign: "top" }}>
                <td style={{ padding: "0.5rem 0.35rem" }}>
                  <div style={{ fontWeight: 600 }}>{c.label}</div>
                  {c.description ? (
                    <div style={{ fontSize: "0.8rem", marginTop: "0.25rem", opacity: 0.85 }}>
                      {c.description.length > 200 ? `${c.description.slice(0, 197)}…` : c.description}
                    </div>
                  ) : null}
                </td>
                <td style={{ padding: "0.5rem 0.35rem", fontFamily: "monospace", fontSize: "0.8rem" }}>
                  {c.connector}
                </td>
                <td style={{ padding: "0.5rem 0.35rem" }}>
                  {c.configured ? (
                    <span aria-label={`${c.label} configured`}>Configured</span>
                  ) : (
                    <span style={{ color: "var(--warn, #b45309)" }} aria-label={`${c.label} not configured`}>
                      Not configured
                    </span>
                  )}
                </td>
                <td style={{ padding: "0.5rem 0.35rem" }}>{labelTier(c.active_tier)}</td>
                <td style={{ padding: "0.5rem 0.35rem", fontSize: "0.85rem" }}>
                  {c.url ? (
                    <div>
                      <span style={{ opacity: 0.7 }}>Server URL: </span>
                      {c.url}
                    </div>
                  ) : null}
                  <div>URL set: {triState(c.url_configured)}</div>
                  <div>Topic set: {triState(c.topic_configured)}</div>
                  <div>Token set: {triState(c.token_configured)}</div>
                  {c.subscription_count != null ? (
                    <div>
                      <span style={{ opacity: 0.7 }}>Browser subscriptions: </span>
                      {c.subscription_count}
                    </div>
                  ) : null}
                  {c.push_service_configured != null ? (
                    <div>VAPID / push service ready: {c.push_service_configured ? "Yes" : "No"}</div>
                  ) : null}
                  {c.updated_at ? (
                    <div>
                      <span style={{ opacity: 0.7 }}>Updated: </span>
                      {c.updated_at}
                    </div>
                  ) : null}
                  {c.key_version != null ? (
                    <div>
                      <span style={{ opacity: 0.7 }}>Key version: </span>
                      {c.key_version}
                    </div>
                  ) : null}
                  {c.why_not_available ? <div style={{ opacity: 0.9 }}>{c.why_not_available}</div> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
