// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";

export interface UserRow {
  id: string;
  email: string;
  role: "admin" | "user";
  disabled: boolean;
}

export function UserPicker({
  value,
  onChange,
  isAdmin: adminOk,
}: {
  value: string;
  onChange: (id: string) => void;
  isAdmin: boolean;
}): JSX.Element {
  const { client } = useAuth();
  const q = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => client.getJson<UserRow[]>("/api/v1/admin/users"),
    enabled: adminOk,
  });

  if (!adminOk) {
    return <p style={{ color: "crimson" }}>User picker is admin-only.</p>;
  }
  if (q.isPending) return <p>Loading users…</p>;
  if (q.isError) return <p>Failed to load users.</p>;

  return (
    <label>
      User
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {q.data?.map((u) => (
          <option key={u.id} value={u.id}>
            {u.email} ({u.role}
            {u.disabled ? ", disabled" : ""})
          </option>
        ))}
      </select>
    </label>
  );
}
