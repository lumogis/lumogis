// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useState, type FormEvent } from "react";

export function CalDavForm({
  value,
  onChange,
  isEdit,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  isEdit: boolean;
}): JSX.Element {
  const [base, setBase] = useState(String(value.base_url ?? ""));
  const [user, setUser] = useState(String(value.username ?? ""));
  const [pass, setPass] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const payload: Record<string, unknown> = {
      base_url: base,
      username: user,
    };
    if (pass.length > 0) payload.password = pass;
    else if (!isEdit) payload.password = "";
    onChange(payload);
  };

  return (
    <form onSubmit={submit} style={{ display: "grid", gap: "0.5rem" }}>
      <label>
        Base URL
        <input value={base} onChange={(e) => setBase(e.target.value)} type="url" required />
      </label>
      <label>
        Username
        <input value={user} onChange={(e) => setUser(e.target.value)} required minLength={1} maxLength={256} />
      </label>
      <label>
        Password
        <input
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          type="password"
          autoComplete="new-password"
          minLength={isEdit ? 0 : 1}
          maxLength={1024}
        />
        {isEdit && <span style={{ fontSize: "0.8rem" }}>Leave blank to keep existing.</span>}
      </label>
      <button type="submit">Apply</button>
    </form>
  );
}
