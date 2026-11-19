// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Pick one household member (admin-only). Rebuilt on the ui Select in LUM-993 plan B
// Pass 6; props and behaviour are unchanged: `value` is a user id ("" = nobody) and
// `onChange` receives the chosen id.
import { useQuery } from "@tanstack/react-query";

import { Alert, AlertDescription } from "../../components/ui/alert";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { useAuth } from "../../auth/AuthProvider";
import { roleLabel } from "../admin/adminUsersDisplay";

export interface UserRow {
  id: string;
  email: string;
  role: "admin" | "user";
  disabled: boolean;
}

function optionLabel(u: UserRow): string {
  return `${u.email} (${roleLabel(u.role)}${u.disabled ? ", turned off" : ""})`;
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
    return (
      <Alert variant="destructive" className="max-w-md">
        <AlertDescription>Only an admin can pick a person here.</AlertDescription>
      </Alert>
    );
  }
  if (q.isPending) return <p className="m-0 text-sm text-muted-foreground">Loading people…</p>;
  if (q.isError) {
    return (
      <Alert variant="destructive" className="max-w-md">
        <AlertDescription>Lumogis couldn&apos;t load the list of people.</AlertDescription>
      </Alert>
    );
  }

  const users = q.data ?? [];
  const labelOf = (id: string | null): string => {
    const hit = users.find((u) => u.id === id);
    return hit ? optionLabel(hit) : "Choose a person";
  };

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor="user-picker">User</Label>
      <Select value={value || null} onValueChange={(v) => onChange(String(v ?? ""))}>
        <SelectTrigger id="user-picker" aria-label="User" className="w-full max-w-md">
          <SelectValue>{(v: string | null) => labelOf(v)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {users.map((u) => (
            <SelectItem key={u.id} value={u.id}>
              {optionLabel(u)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
