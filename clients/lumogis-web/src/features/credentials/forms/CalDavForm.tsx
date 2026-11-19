// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-846 — the password is required on EVERY save, including edits.
//
// This form used to do `if (pass.length > 0) payload.password = pass;` and omit
// the key otherwise. Against a whole-payload-replacing `put_payload` that is not
// "keep the existing password", it is "delete it" — on the most ordinary edit
// there is. See `./secrets.ts` for the full reasoning.
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { Field, FormError } from "./FormParts";
import { REPLACE_NOTICE, isSecretEntered, secretRequiredMessage } from "./secrets";

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
  const [error, setError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    // Validation is owned here rather than by a `required` attribute, and there
    // is one mechanism on purpose: `required` blocks the submit before this
    // runs, making it a dead branch, and its native bubble says "Please fill in
    // this field" where what the user needs to be told is that saving replaces
    // the whole credential.
    if (!isSecretEntered(pass)) {
      setError(secretRequiredMessage(isEdit, "password"));
      return;
    }
    setError(null);
    // Every key, every time. The payload the server stores is exactly this
    // object, so a key missing here is a field missing from the credential.
    onChange({
      base_url: base.trim(),
      username: user.trim(),
      password: pass,
    });
  };

  return (
    <form onSubmit={submit} className="grid gap-3">
      <Field label="Base URL">
        {({ id }) => <Input id={id} value={base} onChange={(e) => setBase(e.target.value)} type="url" required />}
      </Field>
      <Field label="Username">
        {({ id }) => (
          <Input
            id={id}
            value={user}
            onChange={(e) => setUser(e.target.value)}
            required
            minLength={1}
            maxLength={256}
          />
        )}
      </Field>
      <Field label="Password" note={isEdit ? REPLACE_NOTICE : undefined}>
        {({ id, noteId }) => (
          <Input
            id={id}
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            type="password"
            autoComplete="new-password"
            maxLength={1024}
            aria-describedby={noteId}
          />
        )}
      </Field>
      {error && <FormError>{error}</FormError>}
      <Button type="submit" className="justify-self-start">
        Apply
      </Button>
    </form>
  );
}
