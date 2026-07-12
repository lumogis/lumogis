// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin feature-flags: read-only visibility onto the env-var experimental
// gates registry (LUM-126). Surfacing for LUM-573 — operators can see which
// disabled-by-default flags exist and which are active without grepping env
// files. Env-only in v1: this panel does not mutate flags.

import { useQuery } from "@tanstack/react-query";

import { fetchFeatureFlags, type FeatureFlagState } from "../../api/featureFlags";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";

// Per-flag honesty copy shown under the description. Keyed by registry key so
// the note travels with the flag regardless of table order.
const FLAG_NOTES: Record<string, string> = {
  EGRESS_GUARD:
    "Bypassable defence-in-depth — routing policy at the orchestrator remains the primary guarantee. Enabling this does not by itself isolate the network.",
};

export function AdminFeatureFlagsView(): JSX.Element {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ["admin", "feature-flags"],
    queryFn: () => fetchFeatureFlags(client),
  });

  if (q.isPending) {
    return (
      <section aria-busy="true">
        <h1>Feature flags</h1>
        <p>Loading…</p>
      </section>
    );
  }

  if (q.isError) {
    const err = q.error;
    const detail =
      err instanceof ApiError
        ? err.status === 401
          ? "Not signed in or session expired."
          : err.status === 403
            ? "Admin role required for feature flags."
            : err.detail
        : "Feature flags unavailable.";
    return (
      <section>
        <h1>Feature flags</h1>
        <p role="alert">{detail}</p>
      </section>
    );
  }

  const data = q.data;

  return (
    <section className="lumogis-admin-dense-section">
      <h1>Feature flags</h1>
      <p style={{ maxWidth: "42rem", opacity: 0.9 }}>
        Experimental subsystems ship disabled by default and are gated by environment variables.
        This is a read-only window onto the registry — it shows which flags exist and which are
        active in this process. Flags are <strong>set in the host environment</strong> (env file or
        Compose) and take effect on the next orchestrator restart; there is no in-product toggle in
        this release.
      </p>

      <p style={{ fontSize: "0.85rem", opacity: 0.85 }}>
        {data.enabled} of {data.total} flag{data.total === 1 ? "" : "s"} currently enabled.
      </p>

      <div className="lumogis-table-scroll">
        <table className="lumogis-dense-table">
          <thead>
            <tr>
              <th>Flag</th>
              <th>State</th>
              <th>Environment variable</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {data.flags.map((flag: FeatureFlagState) => {
              const note = FLAG_NOTES[flag.key];
              return (
                <tr key={flag.key}>
                  <td style={{ fontFamily: "monospace", fontSize: "0.85rem" }}>{flag.key}</td>
                  <td
                    style={{
                      color: flag.enabled ? "#2e7d32" : "#757575",
                      fontWeight: 600,
                    }}
                  >
                    {flag.enabled ? "enabled" : "disabled"}
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: "0.85rem" }} className="lumogis-long-text">
                    {flag.env_var}
                  </td>
                  <td style={{ fontSize: "0.85rem" }} className="lumogis-long-text">
                    {flag.description}
                    {note ? (
                      <span style={{ display: "block", marginTop: "0.35rem", color: "#ed6c02" }}>
                        {note}
                      </span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
