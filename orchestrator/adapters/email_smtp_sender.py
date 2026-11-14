# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""SMTP submission adapter — the first OUTBOUND email capability (LUM-650).

The literal mirror of the synchronous inbound ``EmailImapSource`` adapter, for
sending: a user's reply / compose-new is delivered **as the user, through their
own provider's SMTP submission endpoint** (Gmail/iCloud/Fastmail) — never a
Lumogis relay, never a home-IP MTA. Provider submission applies the user's own
SPF/DKIM/DMARC, which is the whole deliverability reason to send this way.

Stdlib ``smtplib`` only (zero new dependency); the executor + every email
handler is synchronous and sends run off the request path (proposal → Approvals
→ executor on a worker thread), so a blocking ``smtplib`` call is the correct
fit — an async library would only force event-loop bridging for no benefit.

Security invariants enforced here:

* **From is ALWAYS the authenticated account** — ``build_message`` is fed the
  mailbox's credential username as ``from_addr``; nothing from the client
  payload can set it (anti-spoofing, and required by provider submission which
  rejects/rewrites a From ≠ the authenticated account).
* **Header-injection safe** — MIME is built with :class:`email.message.EmailMessage`
  (the modern API encodes/folds header values); in addition recipient addresses
  and the subject are rejected if they carry CR/LF or other control chars before
  the message is built (defence in depth).
* **Egress-contained** — the submission connect rides ``smtp_egress_scope``
  (symmetric with ``imap_egress_scope``); a block maps to
  :class:`PermanentActionError` so the proposal queue dead-letters instead of
  retrying a config/attack condition forever.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from dataclasses import field
from email.message import EmailMessage
from email.utils import formatdate
from email.utils import make_msgid
from email.utils import parseaddr
from typing import Iterable
from typing import Optional

from actions.errors import PermanentActionError
from services.egress_guard import EgressBlockedError

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttachmentPart:
    """One outbound attachment, resolved from staging at execute time (LUM-695).

    Adapter-level, deliberately not a wire model: attachment **bytes never enter
    the action payload**. The payload carries staged-file *references*; the
    handler resolves them here, at execute time, where the bytes are needed and
    nowhere else.
    """

    filename: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class SendResult:
    """Outcome of a submission. ``refused`` is empty on a full success and, on a
    PARTIAL refusal, carries the rejected addresses — the accepted set still
    received the message, so the handler writes the sent-log for them (audit
    truth) and then surfaces the partial failure. A TOTAL refusal never returns a
    ``SendResult`` (the adapter raises)."""

    message_id: str
    refused: tuple[str, ...] = field(default_factory=tuple)


# Bounded — an outbound submission must never hang a worker thread indefinitely.
_SEND_TIMEOUT_SECONDS = 30.0


class SmtpSender:
    """Send one message through a mailbox's SMTP submission endpoint.

    ``transport_factory`` (tests only) — ``(host, port) -> smtp-like object``
    exposing ``login`` / ``send_message`` / ``quit``. Production leaves it ``None``
    and the port drives the real ``smtplib`` class (465 → ``SMTP_SSL``; else
    ``SMTP`` + ``starttls``).
    """

    def __init__(self, *, transport_factory=None) -> None:
        self._transport_factory = transport_factory

    # ---- MIME ---------------------------------------------------------

    @staticmethod
    def build_message(
        *,
        from_addr: str,
        to: Iterable[str],
        subject: str,
        body: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
        cc: Iterable[str] = (),
        bcc: Iterable[str] = (),
        attachments: Iterable[AttachmentPart] = (),
        max_total_bytes: Optional[int] = None,
    ) -> EmailMessage:
        """Build the outbound message (the SHARED MIME builder — LUM-652's
        Save-to-Drafts must reuse this, not re-implement MIME).

        ``in_reply_to`` / ``references`` are accepted so LUM-651 threading slots
        in without changing the adapter; LUM-650 sets only ``In-Reply-To`` (from
        the reply re-fetch) and leaves ``references`` empty.

        ``cc`` / ``bcc`` (LUM-698) are set as headers. **The ``Bcc`` header is
        correct and safe here** — ``smtplib.send_message`` (which :meth:`send`
        calls with no explicit ``to_addrs``) builds its envelope from the
        ``To`` + ``Bcc`` + ``Cc`` headers and then does
        ``msg_copy = copy.copy(msg); del msg_copy['Bcc']`` before serialising,
        so BCC recipients are delivered but never transmitted. **Do not "fix"
        this by stripping Bcc here or passing an explicit recipient list** — that
        would silently stop delivering to BCC recipients.

        One deliberate asymmetry: ``email_save_draft`` serialises with
        ``msg.as_bytes()``, which does NOT strip ``Bcc``. That is intended — a
        draft lands in the user's OWN mailbox, and real clients keep Bcc in
        drafts so the send can be resumed.

        ``attachments`` (LUM-695) turns the message into ``multipart/mixed``.
        **When it is empty the output is structurally identical to before** —
        same headers, same order, same serialised body — which is what lets
        reply/compose/forward/save-draft all inherit this one change safely.
        That identity can only ever be *structural*: ``make_msgid`` embeds a
        timestamp, a pid and a random token, so literal byte-equality is
        unachievable even between two builds of unchanged code. The regression
        test freezes ``make_msgid`` and diffs against a golden serialisation.
        """
        to_list = [t for t in to]
        cc_list = [c for c in cc]
        bcc_list = [b for b in bcc]
        # A message addressed ONLY via BCC is valid, so the guard is on the union.
        if not (to_list or cc_list or bcc_list):
            raise PermanentActionError("no_recipients", "message has no recipients")

        # Defence in depth: the EmailMessage API already encodes header values,
        # but reject CR/LF / control chars in caller-supplied header material
        # before the message is even built (header-injection belt-and-braces).
        _reject_control_chars("subject", subject)
        for addr in (*to_list, *cc_list, *bcc_list):
            _reject_control_chars("recipient", addr)
        _reject_control_chars("from", from_addr)
        # LUM-651: In-Reply-To / References are supplied from re-fetched
        # original-message headers (attacker-influenceable). The reply handler
        # already sanitizes them, so this is a backstop that protects any future
        # caller of the shared builder — the MIME builder rejects control chars in
        # ALL headers it sets, not just subject/recipients.
        if in_reply_to:
            _reject_control_chars("in_reply_to", in_reply_to)
        if references:
            _reject_control_chars("references", references)

        msg = EmailMessage()
        msg["From"] = from_addr
        if to_list:
            msg["To"] = ", ".join(to_list)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        if bcc_list:
            # Stripped from the transmitted copy by send_message — see docstring.
            msg["Bcc"] = ", ".join(bcc_list)
        msg["Subject"] = subject
        # RFC 5322 §3.6 makes Date mandatory, and `smtplib.send_message` does
        # not add one — it only reads `Resent-Date`. Every message from this
        # sender went out without it, which reads to SpamAssassin as
        # MISSING_DATE (LUM-65 P1 #9 found it on the system mailer; the same
        # omission was here). UTC rather than local time so a self-hosted
        # deployment does not broadcast the household's timezone.
        msg["Date"] = formatdate(usegmt=True)
        # A domain for make_msgid from the From address keeps the Message-ID
        # plausible for the sending account; parseaddr strips any display name.
        _, from_spec = parseaddr(from_addr)
        domain = from_spec.rsplit("@", 1)[-1] if "@" in from_spec else None
        msg["Message-ID"] = make_msgid(domain=domain)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        msg.set_content(body)

        parts = list(attachments)
        if parts:
            _attach_all(msg, parts, max_total_bytes=max_total_bytes)
        return msg

    # ---- transport ----------------------------------------------------

    def send(
        self,
        msg: EmailMessage,
        *,
        host: str,
        port: int,
        username: str,
        app_password: str,
        user_id: Optional[str] = None,
        auth_type: str = "app_password",
        access_token: Optional[str] = None,
    ) -> SendResult:
        """Submit ``msg`` and return a :class:`SendResult`.

        Port-driven TLS (465 → implicit ``SMTP_SSL``; else ``SMTP`` + STARTTLS).
        The connect is egress-scoped. Failures a retry cannot fix (auth, TOTAL
        recipient refusal, egress block, connect refused) raise
        :class:`PermanentActionError` so the queue dead-letters on the first
        failure. A PARTIAL refusal is NOT raised here — the accepted set did
        receive the message; ``SendResult.refused`` names the rejected addresses
        so the handler records the sent-log for the accepted set and then
        surfaces the partial failure (plan §Error handling contract).

        ``auth_type="xoauth2"`` (LUM-686) authenticates with ``AUTH XOAUTH2`` and
        the reused OAuth ``access_token`` instead of ``smtp.login``.
        """
        from services.smtp_transport import smtp_egress_scope

        try:
            with smtp_egress_scope(host, user_id=user_id):
                smtp = self._connect(host, port)
                try:
                    self._authenticate(smtp, username, app_password, auth_type, access_token)
                    # send_message returns a dict of refused recipients; it only
                    # RAISES SMTPRecipientsRefused when EVERY recipient is refused.
                    refused = smtp.send_message(msg) or {}
                finally:
                    try:
                        smtp.quit()
                    except Exception:  # noqa: BLE001 - best-effort close
                        pass
        except EgressBlockedError as exc:
            raise PermanentActionError(
                "egress_blocked", f"SMTP egress blocked for host {host!r}"
            ) from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentActionError("smtp_auth", "SMTP authentication failed") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise PermanentActionError(
                "recipients_refused", f"all recipients refused: {_refused_addrs(exc.recipients)}"
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise PermanentActionError("smtp_connect", "SMTP submission failed") from exc

        return SendResult(
            message_id=str(msg["Message-ID"] or ""),
            refused=tuple(sorted(str(a).lower() for a in refused)),
        )

    def verify(
        self,
        *,
        host: str,
        port: int,
        username: str,
        app_password: str,
        user_id: Optional[str] = None,
        auth_type: str = "app_password",
        access_token: Optional[str] = None,
    ) -> None:
        """Connect + authenticate (NO send) to validate sending credentials for
        the pre-save ``PUT …/smtp`` test. Raises :class:`PermanentActionError`
        with a coarse reason (``smtp_auth`` / ``smtp_connect`` / ``egress_blocked``)
        the route maps to a non-sensitive UI string.

        ``auth_type="xoauth2"`` (LUM-693) authenticates with ``AUTH XOAUTH2`` +
        ``access_token`` via the SAME :meth:`_authenticate` helper ``send()``
        uses (incl. its mandatory pre-AUTH EHLO), instead of ``smtp.login``.
        """
        from services.smtp_transport import smtp_egress_scope

        try:
            with smtp_egress_scope(host, user_id=user_id):
                smtp = self._connect(host, port)
                try:
                    self._authenticate(smtp, username, app_password, auth_type, access_token)
                finally:
                    try:
                        smtp.quit()
                    except Exception:  # noqa: BLE001
                        pass
        except EgressBlockedError as exc:
            raise PermanentActionError("egress_blocked", "SMTP egress blocked") from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentActionError("smtp_auth", "SMTP authentication failed") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise PermanentActionError("smtp_connect", "SMTP connection failed") from exc

    def _authenticate(
        self,
        smtp,
        username: str,
        app_password: str,
        auth_type: str,
        access_token: Optional[str],
    ) -> None:
        """Authenticate ``smtp`` for ``username`` — ``app_password`` via
        ``smtp.login`` (unchanged) or ``xoauth2`` via ``AUTH XOAUTH2`` (LUM-686).

        The xoauth2 branch MUST issue ``ehlo_or_helo_if_needed()`` before the raw
        ``AUTH`` docmd: ``_connect`` leaves no EHLO in effect (port 465 never
        sends one; ``starttls()`` resets the EHLO state per RFC 3207 §4.2), and —
        unlike ``smtp.login()`` — ``docmd`` does not EHLO for us, so a bare
        ``AUTH`` would draw a ``503`` from Gmail/Outlook. A non-235 reply raises
        :class:`smtplib.SMTPAuthenticationError`, reusing ``send``/``verify``'s
        existing auth-failure handler.
        """
        if auth_type == "xoauth2":
            if not access_token:
                raise smtplib.SMTPAuthenticationError(0, b"missing xoauth2 access token")
            from services.email_oauth import build_xoauth2_auth_string

            smtp.ehlo_or_helo_if_needed()
            code, resp = smtp.docmd(
                "AUTH", "XOAUTH2 " + build_xoauth2_auth_string(username, access_token)
            )
            if code != 235:
                raise smtplib.SMTPAuthenticationError(code, resp)
        else:
            smtp.login(username, app_password)

    def _connect(self, host: str, port: int):
        """Port-driven TLS with a verified certificate — see ``connect_smtp``.

        Shared with the system mailer rather than copied: the identical pair of
        un-contexted calls in both adapters is why CWE-295 shipped twice here
        (LUM-65 P2 #11).
        """
        from services.smtp_transport import connect_smtp

        if self._transport_factory is not None:
            return self._transport_factory(host, port)
        return connect_smtp(host, port, timeout=_SEND_TIMEOUT_SECONDS)


def _attach_all(
    msg: EmailMessage, parts: list[AttachmentPart], *, max_total_bytes: Optional[int] = None
) -> None:
    """Add every part as a ``multipart/mixed`` attachment, enforcing the total cap.

    The cap is checked HERE, across the whole message, rather than per-file at
    the staging route: ten separately-legal 20 MB files are only over the limit
    once they are on the same message, and discovering that at SMTP — after the
    user approved the send — is the wrong place to find out.

    Filenames are re-sanitised on the way out. They were already sanitised when
    staged, but this builder is the last point before the value becomes a
    ``Content-Disposition`` parameter on the recipient's side, and it is also
    reachable by any future caller that did not go through staging.
    """
    from services import email_attachments

    total = sum(len(p.data) for p in parts)
    # LUM-715: the caller may pass the sending mailbox's PROVIDER ceiling. Falling
    # back to the global cap for an unknown host is deliberate — inventing a limit
    # for a server whose policy we do not know would refuse legitimate sends.
    limit = max_total_bytes if max_total_bytes is not None else email_attachments.max_total_bytes()
    if total > limit:
        raise PermanentActionError(
            "attachments_too_large",
            f"attachments total {total} bytes, over the {limit} byte limit",
        )

    for index, part in enumerate(parts):
        maintype, _, subtype = (part.mime_type or "").partition("/")
        if not maintype or not subtype:
            # An unusable declared type is not a reason to refuse the send —
            # octet-stream is what every mail client falls back to anyway.
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(
            part.data,
            maintype=maintype,
            subtype=subtype,
            filename=email_attachments.sanitise_filename(part.filename, index=index),
        )


def _reject_control_chars(field: str, value: str) -> None:
    if value is None:
        return
    for ch in str(value):
        if ch in "\r\n" or (ord(ch) < 32 and ch not in "\t"):
            raise PermanentActionError(
                "header_injection", f"{field} contains an illegal control character"
            )


def _refused_addrs(refused) -> str:
    """A safe, comma-joined list of refused recipient addresses (no server text,
    which could leak internal detail)."""
    try:
        return ", ".join(sorted(str(a) for a in (refused or {})))
    except Exception:  # noqa: BLE001
        return "unknown"
