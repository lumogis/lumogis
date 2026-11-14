// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-627 — ImapForm credential payload.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ImapForm } from "../../../src/features/credentials/forms/ImapForm";

describe("ImapForm", () => {
  // LUM-846: this previously asserted the OPPOSITE — that a blank password on
  // edit produced a payload without the key. It was the bug, kept green.
  it("on edit, refuses to submit when the password is left blank", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(
      <ImapForm
        value={{ host: "imap.example.com", port: 993, username: "alice" }}
        onChange={onChange}
        isEdit
      />,
    );
    await u.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/re-enter the password/i);
  });

  it("on edit with the password entered, submits every key", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(
      <ImapForm
        value={{ host: "imap.example.com", port: 993, username: "alice" }}
        onChange={onChange}
        isEdit
      />,
    );
    await u.type(screen.getByLabelText(/^password/i), "app-pass");
    await u.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onChange).toHaveBeenCalledWith({
      host: "imap.example.com",
      port: 993,
      username: "alice",
      use_ssl: true,
      auth_type: "app_password",
      password: "app-pass",
    });
  });

  it("on create, includes password in payload", async () => {
    const onChange = vi.fn();
    const u = userEvent.setup();
    render(<ImapForm value={{}} onChange={onChange} isEdit={false} />);
    await u.type(screen.getByLabelText(/^imap host/i), "imap.gmail.com");
    await u.clear(screen.getByLabelText(/^port/i));
    await u.type(screen.getByLabelText(/^port/i), "993");
    await u.type(screen.getByLabelText(/^username/i), "alice@gmail.com");
    await u.type(screen.getByLabelText(/^password/i), "secret-app-pass");
    await u.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        host: "imap.gmail.com",
        port: 993,
        username: "alice@gmail.com",
        password: "secret-app-pass",
      }),
    );
  });
});
