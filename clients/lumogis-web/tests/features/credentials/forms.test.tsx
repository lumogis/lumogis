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
  it("on edit, omits password from payload when the password field is left blank", async () => {
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
    expect(onChange).toHaveBeenCalledWith({
      base_url: "https://c.example.com/",
      username: "alice",
    });
  });
});

describe("NtfyForm", () => {
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
  it("emits { api_key } on submit when key length is valid (≥16)", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    const key = "a".repeat(20);
    render(<LlmApiKeyForm value={{}} onChange={onChange} />);
    expect(document.querySelector("form.lumogis-credential-form")).toBeTruthy();
    await u.type(screen.getByLabelText(/^api key/i), key);
    await u.click(screen.getByRole("button", { name: /^save$/i }));
    expect(onChange).toHaveBeenCalledWith({ api_key: key });
  });
});
