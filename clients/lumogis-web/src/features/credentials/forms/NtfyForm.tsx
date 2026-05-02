// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useState, type FormEvent } from "react";

const topicRe = /^[a-zA-Z0-9_-]+$/;

export function NtfyForm({
  value,
  onChange,
  isEdit: _isEdit = false,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  isEdit?: boolean;
}): JSX.Element {
  const [url, setUrl] = useState(String(value.url ?? ""));
  const [topic, setTopic] = useState(String(value.topic ?? ""));
  const [token, setToken] = useState(String(value.token ?? ""));
  const [err, setErr] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      void new URL(url);
    } catch {
      setErr("url must be a valid URL");
      return;
    }
    if (topic.length < 1 || topic.length > 64 || !topicRe.test(topic)) {
      setErr("topic: 1–64 chars, [a-zA-Z0-9_-]");
      return;
    }
    const p: Record<string, unknown> = { url, topic };
    if (token.length > 0) p.token = token;
    onChange(p);
  };

  return (
    <form onSubmit={submit} style={{ display: "grid", gap: "0.5rem" }}>
      <label>
        URL
        <input value={url} onChange={(e) => setUrl(e.target.value)} type="url" required />
      </label>
      <label>
        Topic
        <input value={topic} onChange={(e) => setTopic(e.target.value)} required minLength={1} maxLength={64} />
      </label>
      <label>
        Token (optional)
        <input value={token} onChange={(e) => setToken(e.target.value)} type="password" maxLength={512} />
      </label>
      {err && (
        <p role="alert" style={{ color: "salmon" }}>
          {err}
        </p>
      )}
      <button type="submit">Save</button>
    </form>
  );
}
