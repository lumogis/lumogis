// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-769 — turning sending on and off for an already-connected mailbox.
//
// These drive the real component against a mocked `setEmailSmtp`, so every
// assertion is about what the CONTROL does with each of the route's response
// shapes — which is the whole risk surface here. The route's own behaviour is
// covered server-side by LUM-771/772.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../src/api/client";
import type { MailboxAuthMethod } from "../../../src/api/emailSources";
import { MailboxSendControl } from "../../../src/features/signals/MailboxSendControl";
import { deriveSendState, type SendState } from "../../../src/features/signals/sendState";

const setEmailSmtp = vi.fn();
vi.mock("../../../src/api/emailSources", () => ({
  setEmailSmtp: (...args: unknown[]) => setEmailSmtp(...args),
}));

const client = {} as never;

function renderControl(
  over: {
    state?: SendState;
    smtpHostRequired?: boolean;
    authMethod?: MailboxAuthMethod | null;
    mailboxName?: string;
  } = {},
) {
  const onChange = vi.fn();
  render(
    <MailboxSendControl
      client={client}
      sourceId="src-1"
      mailboxName={over.mailboxName ?? "Personal"}
      state={over.state ?? "off"}
      smtpHostRequired={over.smtpHostRequired}
      authMethod={over.authMethod}
      onChange={onChange}
    />,
  );
  return { onChange };
}

function renderAndUnmount(over: Parameters<typeof renderControl>[0]) {
  const { unmount } = render(
    <MailboxSendControl
      client={client}
      sourceId="src-1"
      mailboxName={over?.mailboxName ?? "Personal"}
      state={over?.state ?? "off"}
      authMethod={over?.authMethod}
    />,
  );
  return { unmount };
}

const ok = (over: Record<string, unknown> = {}) => ({
  ok: true,
  enabled: true,
  tested: true,
  error: null,
  ...over,
});

beforeEach(() => {
  setEmailSmtp.mockReset();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("turning sending on", () => {
  it("takes one click for a known provider, with no password field", async () => {
    setEmailSmtp.mockResolvedValue(ok());
    renderControl();

    expect(screen.queryByLabelText(/Sending password/i)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending for Personal/i }));

    await waitFor(() => expect(setEmailSmtp).toHaveBeenCalledTimes(1));
    expect(setEmailSmtp.mock.calls[0][2]).toMatchObject({
      enabled: true,
      reuse_imap_password: true,
    });
    // The common case never sees a form — strictly better than the create form,
    // which asks everyone for a sending password up front.
    expect(screen.queryByLabelText(/Sending password/i)).toBeNull();
  });

  it("also takes one click for an OAuth mailbox", async () => {
    // LUM-777: the happy path for a fixture the wire can produce (`GET /sources`
    // publishes `auth_method: "oauth"`). It does NOT exercise the guard: a
    // successful response never consults `authMethod`, so this passes with or
    // without the LUM-777 code. The guard is pinned by the `smtp_auth` cases
    // below. The server guarantee that an OAuth mailbox verifies with its token,
    // not an empty password, is `test_an_oauth_mailbox_verifies_with_its_token_
    // not_an_empty_password` in `orchestrator/tests/test_smtp_send_route.py`.
    setEmailSmtp.mockResolvedValue(ok());
    renderControl({ authMethod: "oauth" });

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    await waitFor(() => expect(setEmailSmtp).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText(/Sending password/i)).toBeNull();
  });

  it("never reveals a password field for an OAuth mailbox, even if the server answers smtp_auth", async () => {
    // The REGRESSED server: it should answer `smtp_oauth_denied` for an OAuth
    // mailbox (`routes/signals.py`, pinned by `test_smtp_send_route.py`), and here
    // it does not. The client guard is the last line against the password trap.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    setEmailSmtp.mockResolvedValue(ok({ ok: false, enabled: false, error: "smtp_auth" }));
    renderControl({ authMethod: "oauth" });

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    const alert = await screen.findByRole("alert");
    expect(screen.queryByLabelText(/Sending password/i)).toBeNull();
    expect(alert.textContent).toContain("isn't allowing Lumogis to send");
    expect(alert.textContent).not.toContain("wasn't accepted for sending");
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("an app-password mailbox still gets the password field on smtp_auth", async () => {
    // Guards against an over-broad guard (M8: remap for every mailbox).
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    setEmailSmtp.mockResolvedValue(ok({ ok: false, enabled: false, error: "smtp_auth" }));
    renderControl({ authMethod: "app_password" });

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    expect(await screen.findByLabelText(/Sending password for Personal/i)).toBeTruthy();
    expect(warn).not.toHaveBeenCalled();
  });

  it("an unknown method keeps today's reveal on smtp_auth", async () => {
    // Explicit `null`: unknown is never read as OAuth.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    setEmailSmtp.mockResolvedValue(ok({ ok: false, enabled: false, error: "smtp_auth" }));
    renderControl({ authMethod: null });

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    expect(await screen.findByLabelText(/Sending password for Personal/i)).toBeTruthy();
    expect(warn).not.toHaveBeenCalled();
  });

  it("reveals the password field only after the server rejects the reused one", async () => {
    setEmailSmtp.mockResolvedValue(ok({ ok: false, enabled: false, error: "smtp_auth" }));
    renderControl();

    expect(screen.queryByLabelText(/Sending password/i)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    // Revealed by EVIDENCE — the server proved the reused password is not
    // accepted for sending, which is exactly when a separate one is worth asking
    // for. Not predicted, not offered up front.
    expect(await screen.findByLabelText(/Sending password for Personal/i)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("wasn't accepted for sending");
  });

  it("does not ask for a password when the server was merely unreachable", async () => {
    // The password was never the problem, and asking would send the user hunting
    // for a wrong answer.
    setEmailSmtp.mockResolvedValue(ok({ ok: false, enabled: false, error: "smtp_connect" }));
    renderControl();

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("couldn't reach the sending server"),
    );
    expect(screen.queryByLabelText(/Sending password/i)).toBeNull();
  });

  it("does not ask an OAuth mailbox for a password its provider refused", async () => {
    // MUTATION G's target. An Outlook tenant with SMTP AUTH disabled, a missing
    // send scope, or a revoked grant all refuse an OAuth mailbox — which has NO
    // password to give. `smtp_oauth_denied` is the ONLY thing standing between
    // this user and a password field they cannot fill, so collapsing it back
    // into the `smtp_auth` branch re-ships the trap LUM-771 removed.
    setEmailSmtp.mockResolvedValue(ok({ ok: false, enabled: false, error: "smtp_oauth_denied" }));
    renderControl();

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("isn't allowing Lumogis to send");
    // The fix-owner is the provider or the account administrator — NOT the
    // Lumogis operator, who cannot change a Microsoft 365 tenant setting.
    expect(alert.textContent).toContain("manages the email account");
    expect(alert.textContent).not.toContain("Lumogis operator");
    expect(screen.queryByLabelText(/Sending password/i)).toBeNull();
  });

  it("answers a blank submission by name, not with a shrug", async () => {
    // All three are reachable in SHIPPED code today — the setup form's SMTP
    // inputs carry no `required` and it trims a whitespace host to undefined,
    // and the route is rate-limited at 10/60s. Each used to render the generic
    // "you can try again later" fallback.
    for (const [code, expected] of [
      ["smtp_host_required", "sending server's address and port"],
      ["app_password_required", "A sending password is needed"],
      ["rate_limited", "Too many attempts"],
    ] as const) {
      setEmailSmtp.mockReset();
      setEmailSmtp.mockRejectedValue(new ApiError(422, "x", code));
      const { unmount } = render(
        <MailboxSendControl
          client={client}
          sourceId="src-1"
          mailboxName="Personal"
          state="off"
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));
      await waitFor(() => expect(screen.getByRole("alert").textContent).toContain(expected));
      expect(screen.getByRole("alert").textContent).not.toContain("You can try again later.");
      unmount();
    }
  });

  it("shows host and port up front when the server says they are required", async () => {
    setEmailSmtp.mockResolvedValue(ok());
    renderControl({ smtpHostRequired: true });

    // Visible BEFORE any click — zero guaranteed-failing requests. The client
    // never re-implements `provider_for_host`; the server published the answer.
    const host = screen.getByLabelText(/Sending server for Personal/i);
    expect(host).toBeTruthy();
    await userEvent.type(host, "smtp.selfhosted.example");
    await userEvent.type(screen.getByLabelText(/Sending port for Personal/i), "587");
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    await waitFor(() => expect(setEmailSmtp).toHaveBeenCalledTimes(1));
    expect(setEmailSmtp.mock.calls[0][2]).toMatchObject({
      smtp_host: "smtp.selfhosted.example",
      smtp_port: 587,
    });
  });

  it("reveals the host fields when a 422 asks for them", async () => {
    // The fallback for an older orchestrator that never published the flag.
    // Copy alone would be a hard dead end: told a submission host is required,
    // and given nowhere to type one.
    setEmailSmtp.mockRejectedValue(new ApiError(422, "x", "smtp_host_required"));
    renderControl({ smtpHostRequired: false });

    expect(screen.queryByLabelText(/Sending server/i)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    expect(await screen.findByLabelText(/Sending server for Personal/i)).toBeTruthy();
    expect(screen.getByLabelText(/Sending port for Personal/i)).toBeTruthy();
  });

  it("does not show the fields when the flag is absent", async () => {
    // Optional-safe against an older backend: absent reads FALSE. A spurious
    // `true` would show two fields to everyone; a spurious `false` still ends in
    // the 422 above, which reveals them.
    renderControl({});
    expect(screen.queryByLabelText(/Sending server/i)).toBeNull();
  });

  it("says a soft-save is not verified, and reports it to the parent", async () => {
    setEmailSmtp.mockResolvedValue(ok({ tested: false }));
    const { onChange } = renderControl();

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ enabled: true, softSaved: true }));
    expect(screen.getByRole("status").textContent).toContain("couldn't reach the sending server");
  });

  it("renders the shared copy for an HTTP failure", async () => {
    // Asserts REUSE rather than a second vocabulary: this is the exact string
    // `sendOptInMessage.ts` produces, with the card's framing rather than the
    // setup form's.
    setEmailSmtp.mockRejectedValue(new ApiError(503, "x", "mailbox_unreachable"));
    renderControl();

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    // The notice itself, not the live region — the region also holds the state
    // line, by design, so a successful enable is announced rather than silently
    // redrawn.
    await waitFor(() =>
      expect(screen.getByTestId("send-notice-src-1").textContent).toBe(
        "Sending isn't on — Lumogis couldn't reach the mail server just now. " +
          "This usually clears up on its own; you can try again later.",
      ),
    );
  });

  it("never renders the raw error body, on either the known or the fallback path", async () => {
    // MUTATION H's target, and written over BOTH paths from the start: LUM-759
    // shipped this test guarding only the recognised code, and a planted
    // `err.message` fallback left it green — the leak would live in the default
    // branch, which that version never exercised.
    for (const code of ["mailbox_unreachable", "some_code_this_client_has_never_seen"]) {
      setEmailSmtp.mockReset();
      setEmailSmtp.mockRejectedValue(
        new ApiError(503, JSON.stringify({ detail: "SEKRIT-host.internal" }), code),
      );
      const { unmount } = render(
        <MailboxSendControl client={client} sourceId="src-1" mailboxName="Personal" state="off" />,
      );
      await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));
      await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
      expect(document.body.textContent).not.toContain("SEKRIT");
      unmount();
    }
  });

  it("does not retain the sending password after the request", async () => {
    setEmailSmtp.mockResolvedValue(ok({ ok: false, enabled: false, error: "smtp_auth" }));
    renderControl();
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    const field = (await screen.findByLabelText(/Sending password for Personal/i)) as HTMLInputElement;
    await userEvent.type(field, "hunter2");
    expect(field.value).toBe("hunter2");
    expect(field.type).toBe("password");
    expect(field).toHaveAttribute("autocomplete", "off");

    // FAILURE path: the field is still on screen, and must be empty.
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));
    await waitFor(() =>
      expect(
        (screen.getByLabelText(/Sending password for Personal/i) as HTMLInputElement).value,
      ).toBe(""),
    );

    // SUCCESS path: the field goes away entirely, so nothing is retained at all.
    await userEvent.type(screen.getByLabelText(/Sending password for Personal/i), "hunter2");
    setEmailSmtp.mockResolvedValue(ok());
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));
    await waitFor(() => expect(screen.queryByLabelText(/Sending password/i)).toBeNull());
  });
});

describe("turning sending off", () => {
  const BASE_CONFIRM =
    "Lumogis won't be able to send as you from this mailbox. Receiving is unaffected.";
  const CONDITIONAL_SENTENCE =
    " If you gave this mailbox its own sending password, you'll need to enter it " +
    "again to turn sending back on.";

  async function openDialog(name = /Turn off sending for/i) {
    await userEvent.click(screen.getByRole("button", { name }));
    return screen.findByRole("alertdialog");
  }

  it("confirms, states the loss, and does not overclaim the password cost", async () => {
    setEmailSmtp.mockResolvedValue({ ok: true, enabled: false, tested: false, error: null });
    renderControl({ state: "on" });

    const dialog = await openDialog(/Turn off sending for Personal/i);
    const text = dialog.textContent ?? "";
    expect(text).toContain("Turn off sending for Personal?");
    expect(text).toContain("Receiving is unaffected");
    // CONDITIONAL, not a claim. The client cannot see `reuse_imap` and cannot see
    // the mailbox's auth type, so it cannot know whether a password is at stake.
    // An OAuth mailbox re-enables in one click with nothing to retype, and the
    // reuse-the-IMAP-password default has nothing to retype either.
    expect(text).toContain("If you gave this mailbox its own sending password");
    expect(text).not.toMatch(/You will need to (re-)?enter/i);
    expect(setEmailSmtp).not.toHaveBeenCalled();
  });

  it("drops the password sentence for an OAuth mailbox, which never uses one", async () => {
    renderControl({ state: "on", authMethod: "oauth", mailboxName: "Work" });
    const dialog = await openDialog(/Turn off sending for Work/i);
    expect(dialog.textContent).toContain(BASE_CONFIRM);
    expect(dialog.textContent).not.toContain("own sending password");
  });

  it("keeps today's conditional sentence for app-password and unknown", async () => {
    for (const authMethod of ["app_password", null] as const) {
      const { unmount } = renderAndUnmount({ state: "on", authMethod, mailboxName: "Work" });
      const dialog = await openDialog(/Turn off sending for Work/i);
      expect(dialog.textContent).toContain(BASE_CONFIRM + CONDITIONAL_SENTENCE);
      unmount();
    }
  });

  it("sends nothing when the confirm is cancelled", async () => {
    renderControl({ state: "on" });
    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(setEmailSmtp).not.toHaveBeenCalled();
  });

  it("says sending is still on when the disable fails", async () => {
    // Every string in `describeSendOptInFailure` is about turning sending ON, so
    // a failed turn-off routed through the opt-in prefix would have told a user
    // whose sending is still on that they "can try again later" to turn it on.
    // There is also nothing to revert — this control does not update optimistically.
    setEmailSmtp.mockRejectedValue(new ApiError(503, "x", "mailbox_unreachable"));
    renderControl({ state: "on" });

    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Turn off sending" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Sending is still on");
    expect(alert.textContent).not.toContain("isn't on");
    expect(screen.getByText("Sending: on")).toBeTruthy();
  });
});

describe("state rendering", () => {
  it("disables the control when the state is unknown", () => {
    // A failed credentials query means unknown. Offering "Turn on sending" for a
    // mailbox that may already have it on is the thing this state exists to stop.
    renderControl({ state: "unknown" });
    expect(screen.getByRole("button", { name: /sending for Personal/i })).toBeDisabled();
    expect(screen.getByText("Sending: unknown")).toBeTruthy();
  });

  it("says a soft-saved mailbox is not yet verified", () => {
    renderControl({ state: "unverified" });
    expect(screen.getByText("Sending: on — not yet verified")).toBeTruthy();
  });

  it("announces failures as alerts and successes as statuses", async () => {
    // This card splits notice roles on OUTCOME, not on whether the user acted:
    // `deleteMsg` is role="alert" after a user action, `modeMsg` and `syncMsg`
    // are role="status" after one. The state line shares the region so a
    // successful enable is announced rather than silently redrawn.
    setEmailSmtp.mockResolvedValue(ok());
    renderControl();
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Sending is on"));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("native validation gates the request", () => {
  it("does not send a request when a required revealed field is blank", async () => {
    // Found in this chunk's own code review: `required` is INERT without a
    // <form> — there is no submit event for the browser to gate — so the first
    // implementation would have made a guaranteed-failing round-trip to be told
    // what the browser already knew. The enable path is a real form now.
    setEmailSmtp.mockResolvedValue(ok());
    renderControl({ smtpHostRequired: true });

    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    expect(setEmailSmtp).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Sending server for Personal/i)).toBeInvalid();
  });

  it("sends once the required fields are filled", async () => {
    setEmailSmtp.mockResolvedValue(ok());
    renderControl({ smtpHostRequired: true });

    await userEvent.type(
      screen.getByLabelText(/Sending server for Personal/i),
      "smtp.selfhosted.example",
    );
    await userEvent.type(screen.getByLabelText(/Sending port for Personal/i), "587");
    await userEvent.click(screen.getByRole("button", { name: /Turn on sending/i }));

    await waitFor(() => expect(setEmailSmtp).toHaveBeenCalledTimes(1));
  });
});

describe("deriveSendState — the ordering the rendered tests cannot reach", () => {
  // LUM-774. A mutation that lets `softSaved` outrank the credential check passed
  // every rendered test in the suite, because it only misbehaves when the two
  // DISAGREE — and nothing a user can click both drops the credential and
  // refetches it in the same session. Extracting the derivation is what makes the
  // property provable at all; contorting a render fixture to fake the divergence
  // would have been a test more convenient than reality.
  const derive = (over: Partial<Parameters<typeof deriveSendState>[0]> = {}) =>
    deriveSendState({
      credentialsUnavailable: false,
      hasSmtpCredential: false,
      softSaved: false,
      ...over,
    });

  it("says off when the credential is gone, even if this session soft-saved", () => {
    // THE CASE. `softSaved` may only refine a live credential — never assert one.
    expect(derive({ hasSmtpCredential: false, softSaved: true })).toBe("off");
  });

  it("refines a live credential to unverified after a soft-save", () => {
    expect(derive({ hasSmtpCredential: true, softSaved: true })).toBe("unverified");
  });

  it("is plain on for a live credential with no soft-save", () => {
    expect(derive({ hasSmtpCredential: true, softSaved: false })).toBe("on");
  });

  it("is off for no credential and no soft-save", () => {
    expect(derive()).toBe("off");
  });

  it("is unknown whenever the credentials query failed or is still loading, whatever else is true", () => {
    // Never guess "off" from a failed or in-flight read — that would invite a user
    // to turn on something already on. `unknown` outranks everything, including
    // a soft-save.
    for (const hasSmtpCredential of [true, false]) {
      for (const softSaved of [true, false]) {
        expect(derive({ credentialsUnavailable: true, hasSmtpCredential, softSaved })).toBe(
          "unknown",
        );
      }
    }
  });
});
