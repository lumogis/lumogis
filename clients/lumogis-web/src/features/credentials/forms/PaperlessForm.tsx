// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-834 chunk A (G2) — the typed paperless form, replacing the raw-JSON
// textarea `JsonFallbackForm` was giving this connector.
//
// TWO THINGS HERE ARE LOAD-BEARING AND BOTH WERE P0s IN REVIEW.
//
// 1. The payload keys are `base_url` and `token`.
//    `_PAYLOAD_KEYS: tuple[str, str] = ("base_url", "token")` in
//    `services/paperless_credentials.py`. An earlier draft of this form wrote
//    `api_token`, a name read off `PaperlessConnection`'s prose docstring
//    ("base URL + API token") and turned into an identifier — it appears
//    nowhere in the codebase. `PUT` has no per-connector shape validation, so
//    the wrong key SAVES SILENTLY and every later load raises
//    `CredentialUnavailable`: a form that looks like it works, attached to a
//    connector that can never authenticate.
//
// 2. Both fields are required on EVERY save. There is no "leave blank to keep".
//    `put_payload` is whole-payload replacement (`ciphertext =
//    EXCLUDED.ciphertext`, no merge), so a form that omits the secret on edit
//    DESTROYS the stored secret. `CalDavForm`'s `if (pass.length > 0)` has
//    exactly that bug where it already ships (LUM-846) — copy its markup, not
//    its submit. "Leave blank to keep" becomes honest only once the backend can
//    merge, which is chunk B's field-presence work.

import { useState, type FormEvent } from "react";

import { REPLACE_NOTICE, isSecretEntered, secretRequiredMessage } from "./secrets";

/** Ties the token-provenance hint to the token input via `aria-describedby`. */
const TOKEN_HINT_ID = "paperless-token-hint";

export function PaperlessForm({
  value,
  onChange,
  isEdit,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  isEdit: boolean;
}): JSX.Element {
  const [base, setBase] = useState(String(value.base_url ?? ""));
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const baseTrimmed = base.trim();
    const tokenTrimmed = token.trim();
    // Validation is owned HERE, not by a `required` attribute, and there is
    // exactly one mechanism on purpose. `required` would block the submit
    // before this ran — which sounds like belt-and-braces and is actually a
    // dead branch plus a native bubble reading "Please fill in this field",
    // where what the user needs to be told is that saving REPLACES the stored
    // credential. `NtfyForm` is the in-repo precedent for code-owned validation
    // with an inline message; this follows it. A whitespace-only token is also
    // not "entered", which `required` alone would happily accept.
    if (baseTrimmed.length === 0 || !isSecretEntered(token)) {
      setError(secretRequiredMessage(isEdit, "API token"));
      return;
    }
    setError(null);
    // Trimmed on the way out: the loader rejects a value with leading or
    // trailing whitespace, and a token pasted with a trailing newline is the
    // ordinary way to hit that.
    const payload: Record<string, unknown> = {
      base_url: baseTrimmed,
      token: tokenTrimmed,
    };
    onChange(payload);
  };

  return (
    <form onSubmit={submit} style={{ display: "grid", gap: "0.5rem" }}>
      <label>
        Server address
        <input
          value={base}
          onChange={(e) => setBase(e.target.value)}
          type="url"
          placeholder="https://paperless.example"
        />
      </label>
      <label>
        API token
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          type="password"
          autoComplete="new-password"
          maxLength={1024}
          // LUM-282. `aria-describedby`, not a bare sibling <span>: the hint
          // exists for the person setting this up for the first time, and a
          // screen-reader user meets the field before they meet anything
          // sitting after it. Described-by announces it WITH the field.
          aria-describedby={TOKEN_HINT_ID}
        />
      </label>
      {/* Deliberately NOT behind `isEdit`, unlike REPLACE_NOTICE below. The
          audience for "where do I find this?" is whoever has never had a token
          — exactly the `isEdit === false` case. Copying the guard along with
          the styling would have hidden it from everyone who needs it.

          No external link: the user's paperless lives at an address only they
          know, and a generic docs URL is a second thing to keep current. */}
      <span id={TOKEN_HINT_ID} style={{ fontSize: "0.8rem" }}>
        Paperless issues this from your profile menu → <strong>My Profile</strong> →{" "}
        <strong>API Auth Token</strong>.
      </span>
      {isEdit && (
        <span style={{ fontSize: "0.8rem" }}>{REPLACE_NOTICE}</span>
      )}
      {error && (
        <p role="alert" style={{ fontSize: "0.85rem" }}>
          {error}
        </p>
      )}
      <button type="submit">Apply</button>
    </form>
  );
}
