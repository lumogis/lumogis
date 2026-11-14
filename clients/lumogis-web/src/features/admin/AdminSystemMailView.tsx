// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Admin → System mail (LUM-65). A dedicated page rather than a row in
// Connector credentials because the Send test is the point: this is the only
// credential in Lumogis whose configuration is not believed until it has
// demonstrably delivered a message, and that verb has nowhere to live in a
// generic key/value credential form.

import { useEffect, useState, type FormEvent, type JSX } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "../../api/client";
import {
  deleteSystemMail,
  getSystemMail,
  putSystemMail,
  testSystemMail,
  type SystemMailPublic,
} from "../../api/systemMail";
import { useAuth } from "../../auth/AuthProvider";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return "Request failed";
}

export function AdminSystemMailView(): JSX.Element {
  const { client } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin", "system-mail"],
    queryFn: () => getSystemMail(client),
  });

  const [host, setHost] = useState("");
  const [port, setPort] = useState(587);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fromAddr, setFromAddr] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const d = q.data;
    if (!d) return;
    setHost(d.host ?? "");
    setPort(d.port ?? 587);
    setUsername(d.username ?? "");
    setFromAddr(d.from_addr ?? "");
    // Never seeded from the server — it is never sent to us. Blank means
    // "keep the stored one".
    setPassword("");
  }, [q.data]);

  const save = useMutation({
    mutationFn: () =>
      putSystemMail(client, {
        host: host.trim(),
        port: Number(port),
        username: username.trim(),
        // Omitted entirely when blank: an explicit "" is a 422 server-side,
        // precisely so a round-tripped redacted form cannot blank the secret.
        ...(password ? { password } : {}),
        from_addr: fromAddr.trim(),
      }),
    onSuccess: (data) => {
      qc.setQueryData(["admin", "system-mail"], data);
      setPassword("");
      setError(null);
      setNotice(
        data.email_reset_enabled
          ? "Saved."
          : "Saved. Run Send test to enable email password reset.",
      );
    },
    onError: (e) => {
      setNotice(null);
      setError(errMsg(e));
    },
  });

  const test = useMutation({
    mutationFn: () => testSystemMail(client),
    onSuccess: async () => {
      setError(null);
      setNotice("Test message sent. Check the inbox for your admin account.");
      await qc.invalidateQueries({ queryKey: ["admin", "system-mail"] });
    },
    onError: (e) => {
      setNotice(null);
      setError(errMsg(e));
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteSystemMail(client),
    onSuccess: async () => {
      setError(null);
      setNotice("System mail removed. Email password reset is off.");
      await qc.invalidateQueries({ queryKey: ["admin", "system-mail"] });
    },
    onError: (e) => {
      setNotice(null);
      setError(errMsg(e));
    },
  });

  if (q.isLoading) return <p>Loading system mail…</p>;
  if (q.isError) return <p role="alert">{errMsg(q.error)}</p>;

  const data = q.data as SystemMailPublic;
  const busy = save.isPending || test.isPending || remove.isPending;

  function onSubmit(e: FormEvent): void {
    e.preventDefault();
    if (!busy) save.mutate();
  }

  return (
    <section data-testid="admin-system-mail">
      <h1>System mail</h1>
      <p>
        The mailbox Lumogis sends from as itself — password-reset links today. This is
        not a household member’s mailbox: using one would be circular, since the
        account most likely to need a reset email is the one whose credentials would
        be needed to send it.
      </p>

      <EnablementSummary data={data} />

      <form onSubmit={onSubmit} className="lumogis-auth-recovery" style={{ margin: "1rem 0" }}>
        <label htmlFor="system-mail-host">
          SMTP host
          <input
            id="system-mail-host"
            required
            value={host}
            onChange={(e) => setHost(e.target.value)}
            disabled={busy}
          />
        </label>
        <label htmlFor="system-mail-port">
          Port
          <input
            id="system-mail-port"
            type="number"
            min={1}
            max={65535}
            required
            value={port}
            onChange={(e) => setPort(Number(e.target.value))}
            disabled={busy}
          />
        </label>
        <p className="lumogis-auth-recovery__hint">
          Port 465 uses implicit TLS; anything else uses STARTTLS.
        </p>
        <label htmlFor="system-mail-username">
          Username
          <input
            id="system-mail-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={busy}
          />
        </label>
        <label htmlFor="system-mail-password">
          Password
          <input
            id="system-mail-password"
            type="password"
            autoComplete="new-password"
            placeholder={data.password_set ? "•••••••• (leave blank to keep)" : ""}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
          />
        </label>
        <label htmlFor="system-mail-from">
          From address
          <input
            id="system-mail-from"
            type="email"
            required
            value={fromAddr}
            onChange={(e) => setFromAddr(e.target.value)}
            disabled={busy}
          />
        </label>
        {notice !== null && <p role="status">{notice}</p>}
        {error !== null && (
          <p role="alert" className="lumogis-auth-recovery__error">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy} data-testid="system-mail-save">
          {save.isPending ? "Saving…" : "Save"}
        </button>
      </form>

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={() => test.mutate()}
          disabled={busy || !data.configured}
          data-testid="system-mail-test"
        >
          {test.isPending ? "Sending…" : "Send test"}
        </button>
        <button
          type="button"
          onClick={() => remove.mutate()}
          disabled={busy || !data.configured}
          data-testid="system-mail-delete"
        >
          Remove
        </button>
      </div>
    </section>
  );
}

/**
 * Enablement is a conjunction, so an operator staring at a disabled feature
 * needs to see which conjunct is missing. The public status endpoint refuses to
 * say — that is deployment information, and this is where it belongs.
 */
function EnablementSummary({ data }: { data: SystemMailPublic }): JSX.Element {
  const rows: { label: string; ok: boolean; hint: string }[] = [
    {
      label: "SMTP configured",
      ok: data.configured,
      hint: "Fill in the form below and save.",
    },
    {
      label: "Send test passed",
      ok: Boolean(data.last_test_ok_at),
      hint: "Use Send test — changing the host, port, username, password, or From address clears this.",
    },
    {
      label: "Public origin set",
      ok: data.public_origin_set,
      hint: "Set LUMOGIS_PUBLIC_ORIGIN — reset links are built from it and nothing else.",
    },
    {
      label: "Verification key set",
      ok: data.altcha_hmac_set,
      hint: "Set LUMOGIS_ALTCHA_HMAC_KEY — the public request page needs it.",
    },
  ];

  return (
    <div data-testid="system-mail-enablement">
      <p>
        <strong>
          Email password reset:{" "}
          {data.email_reset_enabled ? "enabled" : "not enabled"}
        </strong>
        {data.last_test_ok_at !== null && (
          <> · last successful test {new Date(data.last_test_ok_at).toLocaleString()}</>
        )}
      </p>
      <ul>
        {rows.map((r) => (
          <li key={r.label}>
            {r.ok ? "✓" : "✗"} {r.label}
            {!r.ok && <> — {r.hint}</>}
          </li>
        ))}
      </ul>
    </div>
  );
}
