// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useState, useRef, type FormEvent } from "react";

export function LlmApiKeyForm({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}): JSX.Element {
  const [apiKey, setApiKey] = useState(String(value.api_key ?? ""));
  const [show, setShow] = useState(false);
  const t = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onFocusShow = () => {
    if (t.current) clearTimeout(t.current);
    t.current = setTimeout(() => setShow(true), 200);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (apiKey.length < 16 || apiKey.length > 1024) return;
    onChange({ api_key: apiKey });
  };

  return (
    <form className="lumogis-credential-form" onSubmit={submit}>
      <label>
        API key
        <input
          type={show ? "text" : "password"}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          onFocus={onFocusShow}
          onBlur={() => {
            setShow(false);
            if (t.current) clearTimeout(t.current);
          }}
          minLength={16}
          maxLength={1024}
          autoComplete="new-password"
          spellCheck={false}
          required
        />
      </label>
      <button type="submit">Save</button>
    </form>
  );
}
