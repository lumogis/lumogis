// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { Field, FormError } from "./FormParts";

const topicRe = /^[a-zA-Z0-9_-]+$/;

export function NtfyForm({
  value,
  onChange,
  isEdit = false,
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
    // LUM-846 — the key is ALWAYS sent, and blank means "no token".
    //
    // ntfy's token is genuinely optional, so unlike CalDAV and IMAP this form
    // cannot simply require it. But the old `if (token.length > 0) p.token =
    // token` was the same silent destroy: `put_payload` replaces the whole
    // payload, so a user opening this to fix the URL — with the token field
    // blank, as it always renders — deleted their stored token on save.
    //
    // There is no "keep existing" to offer here even in principle:
    // `ntfy_runtime.py:116` reads `(payload.get("token") or "").strip()`, so an
    // absent token and an empty one are **identical** to the runtime. Two states,
    // not three. So the honest fix is to always carry the key and say out loud
    // what blank means — the harm was the silence, not the semantics.
    onChange({ url, topic, token: token.trim() });
  };

  return (
    <form onSubmit={submit} className="grid gap-3">
      <Field label="URL">
        {({ id }) => <Input id={id} value={url} onChange={(e) => setUrl(e.target.value)} type="url" required />}
      </Field>
      <Field label="Topic">
        {({ id }) => (
          <Input
            id={id}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            required
            minLength={1}
            maxLength={64}
          />
        )}
      </Field>
      <Field
        label="Token (optional)"
        note={
          isEdit
            ? "Saving replaces the whole credential: re-enter your token to keep it, or leave this blank to remove it."
            : "Leave blank for a topic that needs no token."
        }
      >
        {({ id, noteId }) => (
          <Input
            id={id}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            type="password"
            maxLength={512}
            aria-describedby={noteId}
          />
        )}
      </Field>
      {err && <FormError>{err}</FormError>}
      <Button type="submit" className="justify-self-start">
        Save
      </Button>
    </form>
  );
}
