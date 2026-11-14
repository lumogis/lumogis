// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-834 chunk A (G2) — the two P0 pins for the typed paperless form.
 *
 * Both P0s were *wrong facts about the backend stated confidently in a plan*,
 * and both would have shipped with a green suite. So neither expectation is
 * written out here: the key names are read from the backend constant, and the
 * destroy-on-blank rule is asserted through the form's public behaviour.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { getCredentialFormComponent } from "../../../src/features/credentials/forms";
import { JsonFallbackForm } from "../../../src/features/credentials/forms/JsonFallbackForm";
import { PaperlessForm } from "../../../src/features/credentials/forms/PaperlessForm";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(__dirname, "..", "..", "..", "..", "..");

/**
 * The keys the loader actually reads, taken from the loader.
 *
 * A test that hard-codes its expectation cannot catch a form writing the wrong
 * key — it would have passed against the `api_token` version of this form, which
 * saved silently and left the connector unable to authenticate forever.
 */
function backendPayloadKeys(): string[] {
  const src = readFileSync(
    path.join(repoRoot, "orchestrator/services/paperless_credentials.py"),
    "utf8",
  );
  const m = src.match(/_PAYLOAD_KEYS[^=]*=\s*\(([^)]*)\)/);
  if (!m) throw new Error("could not find _PAYLOAD_KEYS in paperless_credentials.py");
  const keys = [...m[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
  if (keys.length === 0) throw new Error(`no keys parsed from ${m[1]}`);
  return keys;
}

describe("PaperlessForm — P0 pin 1: it writes the keys the loader reads", () => {
  it("submits exactly the backend's _PAYLOAD_KEYS", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<PaperlessForm value={{}} onChange={onChange} isEdit={false} />);

    await u.type(screen.getByLabelText(/server address/i), "https://paperless.example");
    await u.type(screen.getByLabelText(/api token/i), "tok-abc");
    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const payload = onChange.mock.calls[0][0] as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(backendPayloadKeys().sort());
  });

  it("trims both values, because the loader rejects surrounding whitespace", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<PaperlessForm value={{}} onChange={onChange} isEdit={false} />);

    await u.type(screen.getByLabelText(/server address/i), "https://paperless.example");
    await u.type(screen.getByLabelText(/api token/i), "  tok-abc  ");
    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).toHaveBeenCalledWith({
      base_url: "https://paperless.example",
      token: "tok-abc",
    });
  });
});

describe("PaperlessForm — P0 pin 2: the secret is required on every save", () => {
  it("does not submit on edit when the token is left blank", async () => {
    // `put_payload` is whole-payload replacement, so submitting without the
    // token would DESTROY the stored credential. `CalDavForm` has that bug
    // where it already ships (LUM-846) — this form must not reproduce it.
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(
      <PaperlessForm
        value={{ base_url: "https://paperless.example" }}
        onChange={onChange}
        isEdit
      />,
    );

    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/re-enter the API token/i);
  });

  it("a whitespace-only token does not count as entered", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(
      <PaperlessForm
        value={{ base_url: "https://paperless.example" }}
        onChange={onChange}
        isEdit
      />,
    );

    await u.type(screen.getByLabelText(/api token/i), "   ");
    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("says on edit that saving replaces the whole credential", () => {
    render(
      <PaperlessForm value={{ base_url: "https://paperless.example" }} onChange={vi.fn()} isEdit />,
    );
    expect(screen.getByText(/saving replaces the whole credential/i)).toBeInTheDocument();
  });
});

describe("PaperlessForm — G2 acceptance", () => {
  it("paperless resolves to the typed form, not JsonFallbackForm", () => {
    // The registry line IS the feature: without it paperless renders a raw
    // JSON textarea, which is the gap this chunk closes.
    expect(getCredentialFormComponent("paperless")).toBe(PaperlessForm);
    expect(getCredentialFormComponent("paperless")).not.toBe(JsonFallbackForm);
  });

  it("an unregistered connector still falls through to the JSON fallback", () => {
    // The fallback is wrapped, so identity comparison is not available —
    // rendering is. This guards against the registry line being added in a way
    // that captures everything.
    render(
      (() => {
        const Fallback = getCredentialFormComponent("somethingelse");
        return <Fallback value={{}} onChange={vi.fn()} isEdit={false} hint="h" />;
      })(),
    );
    expect(screen.queryByLabelText(/server address/i)).not.toBeInTheDocument();
  });
});
