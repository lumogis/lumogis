// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-846 — the fallback form had the same defect as the typed ones, and worse.
//
// Two things combined into a credential that could be destroyed by looking at
// it:
//
//   1. `MeConnectorsView` renders every form with `value={{}}` — the deliberate
//      no-decrypt posture: the server never sends the stored payload back. So
//      on an EDIT this textarea seeded with `"{}"`, presenting an empty object
//      as though it were the current credential.
//   2. There was no submit button. `onBlur` called `onChange` directly, and in
//      `MeConnectorsView` `onChange` **is the PUT**.
//
// So a user could open a connector card, click into the textarea to see what
// was configured, click away, and `put_payload` would replace the whole row
// with `{}`. Not "lose the secret they did not retype" — lose *every field*,
// without typing anything at all.
//
// The fix is three things, and the first is the one that matters:
//
//   * **Saving is explicit.** Blur validates and reports; only the button
//     submits. Every other form in this directory already worked this way.
//   * **On edit, the box says what it is.** It cannot show the stored payload,
//     so it says so rather than showing `{}` and letting the user infer.
//   * **An empty object is refused on edit.** `{}` is never a legitimate
//     credential, and it is the exact shape the seeded default produces — so
//     the one value most likely to be submitted by accident is the one value
//     that cannot be meant.
//
// Honest editing here needs field-presence metadata (LUM-834 G4) so the box can
// be seeded with the keys that are actually set. Until then this form is safe
// to create with and explicit about what editing costs.
import { useCallback, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { FormError } from "./FormParts";

/* eslint-disable react-refresh/only-export-components -- helper is intentionally co-located for the fallback form tests. */

const PAYLOAD_MAX = 64 * 1024;

export function JsonFallbackForm({
  value,
  onChange,
  hint,
  isEdit = false,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  hint?: string;
  isEdit?: boolean;
}): JSX.Element {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [err, setErr] = useState<string | null>(null);

  /** Parse and validate. Returns the payload, or null having set the error. */
  const parse = useCallback((): Record<string, unknown> | null => {
    let p: Record<string, unknown>;
    try {
      p = JSON.parse(text) as Record<string, unknown>;
    } catch {
      setErr("Invalid JSON");
      return null;
    }
    if (p === null || typeof p !== "object" || Array.isArray(p)) {
      setErr("Payload must be a JSON object");
      return null;
    }
    if (JSON.stringify(p).length > PAYLOAD_MAX) {
      setErr("Payload exceeds 64 KiB cap");
      return null;
    }
    if (isEdit && Object.keys(p).length === 0) {
      setErr(
        "This would replace the stored credential with an empty one. " +
          "Enter the full payload, including any secret — this box cannot show what is currently saved.",
      );
      return null;
    }
    setErr(null);
    return p;
  }, [text, isEdit]);

  // Blur reports problems early. It deliberately does NOT save: this used to be
  // the only save path, which is how looking at the box could destroy the row.
  const onBlur = useCallback(() => {
    parse();
  }, [parse]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const p = parse();
    if (p !== null) onChange(p);
  };

  return (
    <form onSubmit={submit} className="grid gap-3">
      {hint && <p className="m-0 text-sm text-muted-foreground">{hint}</p>}
      {isEdit && (
        <p className="m-0 text-sm text-muted-foreground">
          This box does not show the saved credential — it is never sent back to the browser. Saving
          replaces the whole payload, so enter every field, including any secret.
        </p>
      )}
      <Textarea
        aria-label="Credential payload"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
        }}
        onBlur={onBlur}
        rows={8}
        className="w-full font-mono"
      />
      {err && <FormError>{err}</FormError>}
      <Button type="submit" className="justify-self-start">
        Apply
      </Button>
    </form>
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
