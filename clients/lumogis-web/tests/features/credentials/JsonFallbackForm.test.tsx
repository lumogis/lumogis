// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
/**
 * LUM-846 — the fallback form, which had no tests and the worst version of the
 * bug.
 *
 * It is the form every connector gets when nobody has written a typed one, so
 * it is the one a *new* connector lands on. `testconnector` reaches it today
 * with a PUT that succeeds; `smtp` reaches it and 422s because it is
 * multi-instance and the card sends no instance.
 *
 * The defect was not "omits a blank secret". `MeConnectorsView` renders every
 * form with `value={{}}` (the server never sends stored payloads back), so on
 * edit this textarea seeded with `"{}"` — and `onBlur` called `onChange`, which
 * on that surface **is the PUT**. Clicking into the box to see what was
 * configured and clicking away replaced the whole credential with `{}`.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { JsonFallbackForm } from "../../../src/features/credentials/forms/JsonFallbackForm";
import { getCredentialFormComponent } from "../../../src/features/credentials/forms";

describe("JsonFallbackForm — saving is explicit", () => {
  it("blurring the textarea does NOT save, even with a perfectly valid payload", async () => {
    // The whole bug in one assertion. This used to fire `onChange`, and on the
    // connectors surface `onChange` is the PUT.
    //
    // The payload here is deliberately VALID and non-empty. The first version of
    // this test blurred the seeded `{}` and passed — but it passed because the
    // empty-object guard rejected it, not because blur had stopped saving.
    // Restoring blur-submit left it green, which a mutation check caught: it was
    // asserting the other guard, so it proved nothing about this one.
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<JsonFallbackForm value={{}} onChange={onChange} isEdit />);

    const box = screen.getByRole("textbox");
    await u.clear(box);
    await u.type(box, '{{"api_key":"a-real-value"}');
    await u.tab();

    expect(onChange).not.toHaveBeenCalled();
  });

  it("blur still reports invalid JSON, it just does not submit it", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<JsonFallbackForm value={{}} onChange={onChange} isEdit={false} />);

    const box = screen.getByRole("textbox");
    await u.clear(box);
    await u.type(box, "{{not json"); // `{{` is userEvent's escape for a literal `{`
    await u.tab();

    expect(screen.getByRole("alert")).toHaveTextContent(/invalid json/i);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("the Apply button submits a valid payload", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<JsonFallbackForm value={{}} onChange={onChange} isEdit={false} />);

    const box = screen.getByRole("textbox");
    await u.clear(box);
    await u.type(box, '{{"api_key":"abc"}');
    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).toHaveBeenCalledWith({ api_key: "abc" });
  });
});

describe("JsonFallbackForm — an edit cannot silently empty the credential", () => {
  it("refuses the seeded empty object on edit", async () => {
    // `{}` is what the box contains on arrival, so it is the single most likely
    // thing to be submitted by accident — and the one value that cannot be
    // meant, since an empty payload is never a usable credential.
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<JsonFallbackForm value={{}} onChange={onChange} isEdit />);

    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/replace the stored credential with an empty/i);
  });

  it("allows an empty object on CREATE, where there is nothing to destroy", async () => {
    // Written adjacent on purpose: the refusal above is about protecting an
    // existing row, not about `{}` being invalid in itself. A guard that fired
    // on create too would be a different, wrong rule.
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<JsonFallbackForm value={{}} onChange={onChange} isEdit={false} />);

    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).toHaveBeenCalledWith({});
  });

  it("says on edit that the box does not show what is saved", () => {
    render(<JsonFallbackForm value={{}} onChange={vi.fn()} isEdit />);
    expect(screen.getByText(/does not show the saved credential/i)).toBeInTheDocument();
    expect(screen.getByText(/replaces the whole payload/i)).toBeInTheDocument();
  });

  it("rejects a JSON array, which parses but is not a payload", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<JsonFallbackForm value={{}} onChange={onChange} isEdit />);

    const box = screen.getByRole("textbox");
    await u.clear(box);
    await u.type(box, "[[1,2]"); // `[[` is userEvent's escape for a literal `[`
    await u.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/must be a JSON object/i);
  });
});

describe("JsonFallbackForm — reached through the registry", () => {
  it("an unregistered connector resolves to the fallback, and it gets isEdit", async () => {
    // The wrapper in `forms/index.tsx` dropped `isEdit` before LUM-846, so the
    // fallback could not have warned even if it wanted to. This asserts the
    // prop arrives, through the real resolution path rather than by rendering
    // the component directly.
    const Fallback = getCredentialFormComponent("some_future_connector");
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<Fallback value={{}} onChange={onChange} isEdit={true} hint="h" />);

    expect(screen.getByText(/does not show the saved credential/i)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
