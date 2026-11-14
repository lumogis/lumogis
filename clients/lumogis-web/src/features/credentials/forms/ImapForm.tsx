// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-846 — the password is required on EVERY save, including edits. Same fix
// and same reasoning as `CalDavForm`; see `./secrets.ts`.
//
// **This form is currently unreachable for saving, and that is a separate
// defect.** `imap` is multi-instance (`connectors/registry.py`), so
// `put_payload` requires a non-empty `instance` — and `MeConnectorsView` PUTs to
// `{basePath}/{connector}` with no `?instance=`, so every save from the
// Connectors card returns `422 instance_required`. Mailboxes are credentialed
// from the Email signals surface, which does pass an instance. The fix is
// applied here anyway: the code is what a future caller (or a copied form)
// inherits, and a dormant destroy is still a destroy once the surface is wired.
import { useState, type FormEvent } from "react";

import { REPLACE_NOTICE, isSecretEntered, secretRequiredMessage } from "./secrets";

export function ImapForm({
  value,
  onChange,
  isEdit,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  isEdit: boolean;
}): JSX.Element {
  const [host, setHost] = useState(String(value.host ?? ""));
  const [port, setPort] = useState(String(value.port ?? "993"));
  const [username, setUsername] = useState(String(value.username ?? ""));
  const [pass, setPass] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!isSecretEntered(pass)) {
      setError(secretRequiredMessage(isEdit, "password"));
      return;
    }
    setError(null);
    onChange({
      host: host.trim(),
      port: Number(port),
      username: username.trim(),
      use_ssl: true,
      auth_type: "app_password",
      password: pass,
    });
  };

  return (
    <form
      className="lumogis-credential-form"
      onSubmit={submit}
      style={{ display: "grid", gap: "0.5rem" }}
    >
      <label>
        IMAP host
        <input value={host} onChange={(e) => setHost(e.target.value)} required />
      </label>
      <label>
        Port
        <input
          value={port}
          onChange={(e) => setPort(e.target.value)}
          type="number"
          min={1}
          max={65535}
          required
        />
      </label>
      <label>
        Username
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
          minLength={1}
          maxLength={256}
        />
      </label>
      <label>
        Password
        <input
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          type="password"
          autoComplete="new-password"
          maxLength={1024}
        />
        {isEdit && <span style={{ fontSize: "0.8rem" }}>{REPLACE_NOTICE}</span>}
      </label>
      {error && (
        <p role="alert" style={{ fontSize: "0.85rem" }}>
          {error}
        </p>
      )}
      <button type="submit">Apply</button>
    </form>
  );
}
