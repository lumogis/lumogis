// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { Navigate, Outlet } from "react-router-dom";
import { useUser } from "../../auth/AuthProvider";
import { AdminNav } from "./AdminNav";

export function AdminPage(): JSX.Element {
  const user = useUser();
  if (!user) {
    return <p role="status">Loading…</p>;
  }
  if (user.role !== "admin") {
    return <Navigate to="/chat" replace state={{ toast: "Admin only" }} />;
  }

  return (
    <div className="lumogis-subshell lumogis-subshell--admin">
      <AdminNav />
      <div className="lumogis-subshell__content">
        <Outlet />
      </div>
    </div>
  );
}
