# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Transactional system mailer transport (LUM-65).

The outbound leg for mail Lumogis sends *as itself* — password-reset links
today, onboarding mail later (LUM-264). Deliberately NOT
:mod:`adapters.email_smtp_sender`, which sends **as a household member** from
their own mailbox: using a member's mailbox here would be circular, because the
account most likely to need a reset email is the one whose credentials would be
required to send it.

Transport mechanics are **shared** with the mailbox sender, not copied:
:func:`services.smtp_transport.connect_smtp` owns port-driven TLS (465 implicit,
else STARTTLS) with a verified certificate, and the connect is wrapped in
``smtp_egress_scope``. The two adapters previously held byte-identical copies of
that connect, which is why the CWE-295 unverified-TLS defect had to be fixed in
both places at once (LUM-65 P2 #11).

What differs is the identity and the caller: this adapter is reachable only from
the outbox drain job and the authenticated admin Send test, never from a public
request path.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from email.utils import make_msgid
from email.utils import parseaddr
from typing import Protocol

_log = logging.getLogger(__name__)

# Bounded — a hung relay must not pin a drain worker indefinitely.
_SEND_TIMEOUT_SECONDS = 30.0


class SystemMailSendError(Exception):
    """Submission failed. Carries an operator-facing reason, never the body."""


class SystemSmtpSenderPort(Protocol):
    """Port the ``system_mail`` service depends on (never the module itself)."""

    def send(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
    ) -> None: ...


class SystemSmtpSender:
    """Submit one plaintext message through the operator's relay.

    ``transport_factory`` (tests only) — ``(host, port) -> smtp-like object``
    exposing ``login`` / ``send_message`` / ``quit``. Production leaves it
    ``None`` so the port drives the real :mod:`smtplib` class.
    """

    def __init__(self, *, transport_factory=None) -> None:
        self._transport_factory = transport_factory

    @staticmethod
    def build_message(*, from_addr: str, to_addr: str, subject: str, body: str) -> EmailMessage:
        """Plaintext only — no HTML alternative, no tracking, no attachments.

        A reset mail is two lines and a URL. Keeping it text/plain means there
        is no remote content for a mail client to fetch and nothing that can
        render the link as different text than it points at.

        ``Date`` and ``Message-ID`` are mandatory under RFC 5322 §3.6 and were
        both absent (LUM-65 P1 #9). `smtplib.send_message` does not supply them
        — it reads ``Resent-Date`` only, to pick a header block — so the message
        went out without either. Relays that do not rewrite headers hand that to
        SpamAssassin as MISSING_DATE + MISSING_MID, which is enough to file a
        password reset under junk. A reset mail nobody finds is a reset that
        did not happen.

        ``Date`` is UTC rather than local time: a self-hosted deployment should
        not broadcast the household's timezone to every relay in the path, and
        RFC 5322 accepts either.
        """
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg["Date"] = formatdate(usegmt=True)
        # Domain taken from the sender so the id is plausible for the account
        # it claims to come from; parseaddr strips any display name. Same
        # derivation as the mailbox sender, deliberately.
        _, from_spec = parseaddr(from_addr)
        domain = from_spec.rsplit("@", 1)[-1] if "@" in from_spec else None
        msg["Message-ID"] = make_msgid(domain=domain)
        msg.set_content(body)
        return msg

    def _connect(self, host: str, port: int):
        """Port-driven TLS with a verified certificate — see ``connect_smtp``.

        An operator with a self-signed relay cert will now see a connect failure
        where they previously saw silent success. That is the correct direction
        for a credential-bearing channel: fix the trust store, do not un-verify
        the transport.
        """
        from services.smtp_transport import connect_smtp

        if self._transport_factory is not None:
            return self._transport_factory(host, port)
        return connect_smtp(host, port, timeout=_SEND_TIMEOUT_SECONDS)

    def send(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
    ) -> None:
        """Submit; raise :class:`SystemMailSendError` on any failure.

        Every failure mode collapses to one exception type carrying a short
        reason. The caller (the drain job, or the admin Send test) decides what
        to do with it; neither ever reflects a relay's message to an
        unauthenticated caller, so there is no oracle in the detail.
        """
        from services.smtp_transport import smtp_egress_scope

        msg = self.build_message(from_addr=from_addr, to_addr=to_addr, subject=subject, body=body)
        try:
            with smtp_egress_scope(host):
                smtp = self._connect(host, int(port))
                try:
                    if username:
                        smtp.login(username, password)
                    smtp.send_message(msg)
                finally:
                    try:
                        smtp.quit()
                    except Exception:  # noqa: BLE001 - best-effort close
                        pass
        except smtplib.SMTPAuthenticationError as exc:
            raise SystemMailSendError("smtp_auth: authentication rejected") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise SystemMailSendError("recipients_refused") from exc
        except (smtplib.SMTPException, OSError) as exc:
            # Includes socket.timeout and connection refusal. The message is
            # deliberately short: it is surfaced to an admin and stored in
            # `system_mail_outbox.last_error`, which must never echo the body.
            raise SystemMailSendError(f"smtp_connect: {type(exc).__name__}") from exc
        except Exception as exc:  # noqa: BLE001 - egress block and anything unforeseen
            raise SystemMailSendError(f"send_failed: {type(exc).__name__}") from exc
