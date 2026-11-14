// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-769 — turn outbound sending on and off for one already-connected mailbox.
//
// Until now `setEmailSmtp` had exactly one caller, inside the create form, so
// "let Lumogis send as me" was a decision made once while connecting a mailbox
// and never revisitable: the only route back was to remove and re-add it.
//
// SHAPE: copied from `AttachmentIngestToggle` deliberately. State arrives as a
// prop from the parent's one query; this component owns only `busy`, the
// revealed-field state and its notice. No query per component, so a household
// with six mailboxes still issues one credentials request, not six.
//
// NOT A SWITCH (ADR Decision 2). The interaction is asymmetric — off is always
// instant, on is one click for the two default paths and a short form for the
// rest — and a switch that sometimes opens a form is the toggle anti-pattern.
// A state line plus a named action button matches the card's existing mode
// control, and the user can read what a click will do before making it.

import { useCallback, useState } from "react";

import type { ApiClient } from "../../api/client";
import { setEmailSmtp } from "../../api/emailSources";
import type { MailboxAuthMethod } from "../../api/emailSources";
import type { SendState } from "./sendState";
import {
  describeSendOptInFailure,
  describeSendVerifyFailure,
  SEND_CARD_PREFIX,
  SEND_DISABLE_PREFIX,
} from "./sendOptInMessage";

export interface MailboxSendControlProps {
  client: ApiClient;
  sourceId: string;
  mailboxName: string;
  state: SendState;
  /**
   * From `GET /sources` (LUM-771). `true` means the provider registry has no
   * entry for this mailbox's host — or the server could not parse it — so the
   * submission host and port must be supplied. Never a diagnosis; see
   * `SourceRow.smtp_host_required`.
   */
  smtpHostRequired?: boolean;
  /**
   * From `GET /sources` (LUM-777); see `SourceRow.auth_method`. `null` and absent
   * both mean UNKNOWN. Display-only: the server still refuses with
   * `smtp_oauth_denied`, so this only decides what the control SHOWS.
   */
  authMethod?: MailboxAuthMethod | null;
  /** Fires after any successful mutation. `softSaved` is the egress soft-save. */
  onChange?: (result: { enabled: boolean; softSaved: boolean }) => void;
}

const STATE_LABEL: Record<SendState, string> = {
  on: "Sending: on",
  // The soft-save must never read as a plain "on": the opt-in was persisted but
  // the connect-test could not run, so nothing has proven this mailbox can
  // actually send.
  unverified: "Sending: on — not yet verified",
  off: "Sending: off",
  unknown: "Sending: unknown",
};

export function MailboxSendControl({
  client,
  sourceId,
  mailboxName,
  state,
  smtpHostRequired = false,
  authMethod = null,
  onChange,
}: MailboxSendControlProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // A failure notice must be announced as an alert and a success as a status:
  // this card splits on OUTCOME, not on whether the user acted. `deleteMsg` is
  // `role="alert"` after a user action; `modeMsg` and `syncMsg` are
  // `role="status"` after one.
  const [noticeIsFailure, setNoticeIsFailure] = useState(false);
  // Revealed by evidence, never up front: only after the server proves the
  // reused mailbox password is not accepted for sending.
  const [showPassword, setShowPassword] = useState(false);
  const [password, setPassword] = useState("");
  // Revealed by a published fact — or, if the server never published it, by the
  // 422 that says so. Without the second path an older orchestrator produces a
  // dead end: told a host is required, given nowhere to type one.
  const [showHost, setShowHost] = useState(smtpHostRequired);
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("");

  const isOn = state === "on" || state === "unverified";

  const say = useCallback((text: string, failure: boolean) => {
    setNotice(text);
    setNoticeIsFailure(failure);
  }, []);

  const enable = useCallback(() => {
    setBusy(true);
    setNotice(null);
    const reuse = !showPassword;
    setEmailSmtp(client, sourceId, {
      enabled: true,
      reuse_imap_password: reuse,
      app_password: reuse ? undefined : password,
      smtp_host: showHost ? smtpHost.trim() || undefined : undefined,
      smtp_port: showHost && smtpPort ? Number(smtpPort) : undefined,
    })
      .then((res) => {
        // The connect-test ran and failed. Nothing was persisted, so the state
        // is unchanged and the only job is saying which failure it was.
        if (!res.ok) {
          // LUM-777: an OAuth mailbox never shows a password field, even if the
          // server regresses and answers `smtp_auth`. This mirrors the server's
          // own remap (`routes/signals.py`, pinned by `test_smtp_send_route.py`).
          // Masking a server regression with plausible copy is a conscious trade
          // for "never offer an OAuth user a password field"; the server test and
          // the console warning are what keep a regression visible.
          const remapped = authMethod === "oauth" && res.error === "smtp_auth";
          const code = remapped ? "smtp_oauth_denied" : res.error;
          if (remapped) {
            console.warn(
              "[lumogis-web] smtp_auth for an OAuth mailbox; the server should answer smtp_oauth_denied (LUM-771)",
            );
          }
          say(describeSendVerifyFailure(code), true);
          // Reveal the password field ONLY for `smtp_auth`. Not for
          // `smtp_connect` — the password was never the problem, and asking
          // would send the user hunting for a wrong answer. Not for
          // `smtp_oauth_denied` — an OAuth mailbox has no password to give, and
          // offering a field there is the exact trap LUM-771 exists to remove.
          if (code === "smtp_auth") setShowPassword(true);
          return;
        }
        // Persisted. `tested:false` here is the egress soft-save.
        onChange?.({ enabled: true, softSaved: !res.tested });
        say(
          res.tested
            ? "Sending is on for this mailbox."
            : "Saved, but Lumogis couldn't reach the sending server to check it — " +
                "this deployment blocks outbound connections. Sending may not work yet.",
          false,
        );
        setShowPassword(false);
      })
      .catch((err: unknown) => {
        say(describeSendOptInFailure(err, SEND_CARD_PREFIX), true);
        // `smtp_host_required` is the one code the client can act on directly.
        // Reveal the fields it is asking for — copy alone would be a dead end.
        if (err && typeof err === "object" && "code" in err) {
          if ((err as { code?: string }).code === "smtp_host_required") setShowHost(true);
        }
      })
      .finally(() => {
        // Never retained past the request, on either outcome.
        setPassword("");
        setBusy(false);
      });
  }, [
    client,
    sourceId,
    authMethod,
    showPassword,
    password,
    showHost,
    smtpHost,
    smtpPort,
    onChange,
    say,
  ]);

  const disable = useCallback(() => {
    // Confirm unconditionally. For app-password and unknown mailboxes the client
    // cannot see `reuse_imap` (the credential projection is metadata-only), so it
    // cannot know whether turning off destroys a password the user typed. The
    // last sentence is therefore CONDITIONAL — true for every such mailbox and
    // overclaiming for none.
    //
    // LUM-777: an OAuth mailbox is the exception the client can now see. It
    // NEVER uses a stored sending password (`load_smtp_connection` authenticates
    // it with its token and ignores any stored `app_password`), so there is
    // nothing to retype and it re-enables in one click. Say nothing about one.
    const base =
      `Turn off sending for ${mailboxName}? Lumogis won't be able to send as you ` +
      `from this mailbox. Receiving is unaffected.`;
    const ok = window.confirm(
      authMethod === "oauth"
        ? base
        : `${base} If you gave this mailbox its own sending password, you'll ` +
            `need to enter it again to turn sending back on.`,
    );
    if (!ok) return;
    setBusy(true);
    setNotice(null);
    setEmailSmtp(client, sourceId, { enabled: false })
      .then(() => {
        onChange?.({ enabled: false, softSaved: false });
        say("Sending is off for this mailbox.", false);
        setShowPassword(false);
      })
      // A failed disable leaves sending ON, so it must not be framed as "sending
      // isn't on yet" and must not offer to turn on what is already on. There is
      // nothing to revert either: this control does not update optimistically.
      .catch((err: unknown) => say(describeSendOptInFailure(err, SEND_DISABLE_PREFIX), true))
      .finally(() => setBusy(false));
  }, [client, sourceId, mailboxName, authMethod, onChange, say]);

  return (
    <div className="lumogis-mailbox-send" data-testid={`send-${sourceId}`}>
      {/* The state line lives INSIDE the live region with the notice, so a
          successful enable is announced rather than silently redrawn — the line
          changing from "off" to "on" is otherwise a visual-only change. */}
      <div role={noticeIsFailure ? "alert" : "status"}>
        <span className="lumogis-mailbox-send__state">{STATE_LABEL[state]}</span>
        {notice ? (
          <p className="lumogis-mailbox-send__notice" data-testid={`send-notice-${sourceId}`}>
            {notice}
          </p>
        ) : null}
      </div>

      {/* The enable path is a real <form>. Found in this chunk's own code review:
          `required` on an input is INERT without one — there is no submit event
          for the browser to gate — so the plan's "the browser catches the empty
          case before a request is made" would have been decorative, and every
          blank submission would have made a round-trip to be told what the
          browser already knew. The disable path is a plain button below: it has
          no inputs, and requiring a host to turn sending OFF would be absurd. */}
      {isOn ? (
        <button
          type="button"
          aria-label={`Turn off sending for ${mailboxName}`}
          onClick={disable}
          disabled={busy}
        >
          Turn off sending
        </button>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            enable();
          }}
        >
          {showHost ? (
            <div className="lumogis-mailbox-send__fields">
              <label style={{ display: "block" }}>
                Sending server
                <input
                  type="text"
                  aria-label={`Sending server for ${mailboxName}`}
                  value={smtpHost}
                  onChange={(e) => setSmtpHost(e.target.value)}
                  placeholder="smtp.example.com"
                  required
                />
              </label>
              <label style={{ display: "block" }}>
                Port
                <input
                  type="number"
                  aria-label={`Sending port for ${mailboxName}`}
                  value={smtpPort}
                  onChange={(e) => setSmtpPort(e.target.value)}
                  placeholder="587"
                  required
                />
              </label>
            </div>
          ) : null}

          {showPassword ? (
            <label style={{ display: "block" }}>
              Separate sending password
              <input
                type="password"
                aria-label={`Sending password for ${mailboxName}`}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
                required
              />
            </label>
          ) : null}

          <button
            type="submit"
            aria-label={`Turn on sending for ${mailboxName}`}
            disabled={busy || state === "unknown"}
          >
            Turn on sending
          </button>
        </form>
      )}
    </div>
  );
}
