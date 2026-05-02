// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import type { ReactNode } from "react";
import { useUser } from "../../auth/AuthProvider";

export function RoleGate({ role, children }: { role: "admin" | "user"; children: ReactNode }): JSX.Element | null {
  const u = useUser();
  if (u?.role !== role) return null;
  return <>{children}</>;
}
