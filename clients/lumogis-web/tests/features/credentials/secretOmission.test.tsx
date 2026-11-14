// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-846 — the invariant, asserted across the whole form registry.
 *
 * ## Why this file exists rather than three more per-form tests
 *
 * The bug reached **three** forms. The ticket found one (`CalDavForm`) and its
 * "Done looks like" said to check the others — `ImapForm` had the identical two
 * lines and `NtfyForm` a variant, and the per-form tests for two of them were
 * **green while pinning the defect as intended behaviour**.
 *
 * So per-form tests are not the guard here. They test the forms that exist; the
 * failure mode is the form somebody adds next, copying a neighbour. This file
 * walks the registry and asserts the property of every entry, so a fourth form
 * is covered the moment it is registered rather than when someone remembers.
 *
 * ## The invariant
 *
 * **Leaving a secret field blank must never produce a payload with fewer keys.**
 *
 * `put_payload` is whole-payload replacement — no merge — so a key absent from
 * the submitted payload is a field *deleted* from the stored credential. A form
 * may legitimately refuse to submit (CalDAV, IMAP, Paperless) or submit the key
 * with an empty value (ntfy, whose token is genuinely optional and for whom
 * absent and empty are identical at the runtime). What it may not do is quietly
 * emit a smaller object.
 *
 * Stated as a comparison rather than as a fixed key list on purpose: the test
 * derives the expected keys from the form's own filled-in submission, so it
 * needs no knowledge of any connector's payload shape and cannot go stale when
 * one changes.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { credentialFormRegistry } from "../../../src/features/credentials/forms";

/** Fill an input with something its type will accept. */
async function fillGenerically(
  u: ReturnType<typeof userEvent.setup>,
  input: HTMLInputElement,
): Promise<void> {
  const value =
    input.type === "url"
      ? "https://host.example"
      : input.type === "number"
        ? "993"
        : // Satisfies ntfy's topic regex `[a-zA-Z0-9_-]+` as well as free text.
          "abc";
  await u.clear(input);
  await u.type(input, value);
}

/**
 * Render one form in edit mode, fill every field except (optionally) the
 * secrets, submit, and return the payload it emitted — or `null` if it refused.
 */
async function submitEditForm(
  Form: React.ComponentType<{
    value: Record<string, unknown>;
    onChange: (v: Record<string, unknown>) => void;
    isEdit: boolean;
  }>,
  { fillSecrets }: { fillSecrets: boolean },
): Promise<Record<string, unknown> | null> {
  const onChange = vi.fn();
  const u = userEvent.setup();
  const { container, unmount } = render(
    <Form value={{}} onChange={onChange} isEdit={true} />,
  );

  const inputs = Array.from(container.querySelectorAll("input")) as HTMLInputElement[];
  for (const input of inputs) {
    if (input.type === "password") {
      if (fillSecrets) await u.type(input, "a-real-secret");
      continue;
    }
    await fillGenerically(u, input);
  }

  await u.click(screen.getByRole("button", { name: /^(apply|save)$/i }));
  const payload = onChange.mock.calls.length
    ? (onChange.mock.calls[0][0] as Record<string, unknown>)
    : null;
  unmount();
  return payload;
}

const entries = Object.entries(credentialFormRegistry);

describe("credential forms — a blank secret never shrinks the payload", () => {
  it("the registry is not empty, so the cases below are not vacuous", () => {
    // Without this, a registry that failed to import would make every `it.each`
    // below silently pass by iterating nothing.
    expect(entries.length).toBeGreaterThan(0);
  });

  it.each(entries)("%s", async (_id, Form) => {
    const filled = await submitEditForm(Form, { fillSecrets: true });
    expect(filled, "the form refused a fully-filled submission").not.toBeNull();

    const blank = await submitEditForm(Form, { fillSecrets: false });

    // Refusing is a valid answer — nothing is written, nothing is destroyed.
    if (blank === null) return;

    // Submitting is also valid, as long as no key went missing. A smaller
    // object here is a field deleted from the stored credential.
    expect(Object.keys(blank).sort()).toEqual(Object.keys(filled!).sort());
  });

  it.each(entries)("%s renders at least one password input to guard", (_id, Form) => {
    // Guards the guard. If a form stopped using `type="password"`, the case
    // above would fill nothing, compare two identical submissions, and pass
    // while asserting nothing at all.
    const { container, unmount } = render(<Form value={{}} onChange={vi.fn()} isEdit={true} />);
    const secrets = container.querySelectorAll('input[type="password"]');
    expect(secrets.length).toBeGreaterThan(0);
    unmount();
  });
});
