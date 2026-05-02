// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useState } from "react";
import { useUser } from "../../auth/AuthProvider";
import { MeConnectorsView } from "../me/MeConnectorsView";
import { UserPicker } from "../_shared/UserPicker";

type Tab = "user" | "household" | "system";

const householdBase = "/api/v1/admin/connector-credentials/household";
const systemBase = "/api/v1/admin/connector-credentials/system";

export function AdminConnectorCredentialsView(): JSX.Element {
  const user = useUser();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("user");
  const [userId, setUserId] = useState("");

  const perUserPath =
    userId.length > 0 ? `/api/v1/admin/users/${encodeURIComponent(userId)}/connector-credentials` : null;

  return (
    <section>
      <h2>Connector credentials</h2>
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
        {(["user", "household", "system"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            style={{ fontWeight: tab === t ? 700 : 400 }}
          >
            {t === "user" ? "Per-user" : t}
          </button>
        ))}
      </div>
      {tab === "user" && (
        <>
          <UserPicker value={userId} onChange={setUserId} isAdmin={isAdmin} />
          {!userId && <p role="status">Select a user to load credentials.</p>}
          {perUserPath && <MeConnectorsView basePath={perUserPath} />}
        </>
      )}
      {tab === "household" && (
        <>
          <p role="status" style={{ marginBottom: "0.75rem" }}>
            Household-tier credentials apply to the entire household. Edit only when there is no per-user or system value
            (see server precedence rules).
          </p>
          <MeConnectorsView basePath={householdBase} />
        </>
      )}
      {tab === "system" && (
        <>
          <p role="status" style={{ marginBottom: "0.75rem" }}>
            System-tier credentials apply to all users at the orchestrator level. Edit only when there is no per-user or
            household value.
          </p>
          <MeConnectorsView basePath={systemBase} />
        </>
      )}
    </section>
  );
}
