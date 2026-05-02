// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useCallback, useState, type FormEvent } from "react";

/* eslint-disable react-refresh/only-export-components -- helper is intentionally co-located for the fallback form tests. */

const PAYLOAD_MAX = 64 * 1024;

export function JsonFallbackForm({
  value,
  onChange,
  hint,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  hint?: string;
}): JSX.Element {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [err, setErr] = useState<string | null>(null);

  const onBlur = useCallback(() => {
    try {
      const p = JSON.parse(text) as Record<string, unknown>;
      if (JSON.stringify(p).length > PAYLOAD_MAX) {
        setErr("Payload exceeds 64 KiB cap");
        return;
      }
      setErr(null);
      onChange(p);
    } catch {
      setErr("Invalid JSON");
    }
  }, [text, onChange]);

  return (
    <div>
      {hint && <p style={{ fontSize: "0.85rem" }}>{hint}</p>}
      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
        }}
        onBlur={onBlur}
        rows={8}
        style={{ width: "100%", fontFamily: "monospace" }}
      />
      {err && (
        <p role="alert" style={{ color: "salmon" }}>
          {err}
        </p>
      )}
    </div>
  );
}

export function onJsonSubmit(
  e: FormEvent,
  text: string,
  onValid: (v: Record<string, unknown>) => void,
): void {
  e.preventDefault();
  try {
    onValid(JSON.parse(text) as Record<string, unknown>);
  } catch {
    /* handled in UI */
  }
}
