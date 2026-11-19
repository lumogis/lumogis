// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — credential forms: CalDAV payload, Ntfy URL/topic rules, Llm API key.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CalDavForm } from "../../../src/features/credentials/forms/CalDavForm";
import { LlmApiKeyForm } from "../../../src/features/credentials/forms/LlmApiKeyForm";
import { NtfyForm } from "../../../src/features/credentials/forms/NtfyForm";

describe("CalDavForm", () => {
  // LUM-846: this block previously asserted the OPPOSITE — that a blank
  // password on edit produced a payload without the key. That was not a stale
  // test; it was the bug, written down and kept green. `put_payload` replaces
  // the whole payload, so the payload it asserted DELETED the stored password.
  it("on edit, refuses to submit when the password is left blank", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(
      <CalDavForm
        value={{ base_url: "https://c.example.com/", username: "alice" }}
        onChange={onChange}
        isEdit
      />,
    );
    await u.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/re-enter the password/i);
  });

  it("tells the user on edit that saving replaces the whole credential", () => {
    render(
      <CalDavForm
        value={{ base_url: "https://c.example.com/", username: "alice" }}
        onChange={vi.fn()}
        isEdit
      />,
    );
    expect(screen.getByText(/saving replaces the whole credential/i)).toBeInTheDocument();
  });

  it("submits every key, including the password, when it is entered", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(
      <CalDavForm
        value={{ base_url: "https://c.example.com/", username: "alice" }}
        onChange={onChange}
        isEdit
      />,
    );
    await u.type(screen.getByLabelText(/^password/i), "s3cret");
    await u.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onChange).toHaveBeenCalledWith({
      base_url: "https://c.example.com/",
      username: "alice",
      password: "s3cret",
    });
  });

  it("a whitespace-only password does not count as entered", async () => {
    // `required` accepts "   " happily, and every loader in this codebase
    // rejects a whitespace-padded secret — so letting one through would save a
    // credential that can never authenticate.
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(
      <CalDavForm
        value={{ base_url: "https://c.example.com/", username: "alice" }}
        onChange={onChange}
        isEdit
      />,
    );
    await u.type(screen.getByLabelText(/^password/i), "   ");
    await u.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("NtfyForm", () => {
  it("says on edit that saving replaces the whole credential, tied to the token field", () => {
    render(<NtfyForm value={{ url: "https://ntfy.sh", topic: "home" }} onChange={vi.fn()} isEdit />);
    const input = screen.getByLabelText(/^token/i);
    const note = screen.getByText(/re-enter your token to keep it/i);
    expect(input.getAttribute("aria-describedby")).toBe(note.id);
  });

  it("rejects an invalid topic with an inline error (does not call onChange)", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<NtfyForm value={{}} onChange={onChange} isEdit={false} />);
    await u.type(screen.getByLabelText(/^url/i), "https://ntfy.sh");
    await u.type(screen.getByLabelText(/^topic/i), "bad topic!");
    await u.click(screen.getByRole("button", { name: /^save$/i }));
    expect(screen.getByText(/1–64 chars, \[a-zA-Z0-9_-\]/i)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("LlmApiKeyForm", () => {
  it("rejects a too-short key without calling onChange", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<LlmApiKeyForm value={{}} onChange={onChange} />);
    await u.type(screen.getByLabelText(/^api key/i), "short");
    await u.click(screen.getByRole("button", { name: /^save$/i }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("emits { api_key } on submit when key length is valid (≥16)", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    const key = "a".repeat(20);
    render(<LlmApiKeyForm value={{}} onChange={onChange} />);
    await u.type(screen.getByLabelText(/^api key/i), key);
    await u.click(screen.getByRole("button", { name: /^save$/i }));
    expect(onChange).toHaveBeenCalledWith({ api_key: key });
  });
});
