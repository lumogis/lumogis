# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""SMTP submission egress containment (LUM-650) — symmetric with the IMAP scope.

``smtp_egress_scope`` is the outbound-send analogue of
``egress_guard.imap_egress_scope``: it wraps the ``smtplib`` connect so Core's
only permitted egress target for that call is the mailbox's SMTP submission
host. It reuses the same LUM-625 Layer-1 ``tethered`` mechanism and the same
``IMAP_EGRESS_GUARD`` toggle (enabling email egress containment is one decision,
not two) — no new flag, no new env var.

``declared_smtp_hosts`` / ``household_smtp_hosts`` feed the LUM-571 process-wide
ceiling the same way ``declared_imap_hosts`` does, so the submission host is a
member of the ceiling superset and the scoped connect is not self-blocked.

Honest posture (mirrors ADR-153 / LUM-625): in-process ⇒ bypassable by a fully
compromised Core; this is defence-in-depth + a correctness gate, not an external
wall. Fail-safe-to-functional: if the flag is off or ``tethered`` cannot be
imported, sending still works.
"""

from __future__ import annotations

import contextlib
import logging
import smtplib
import ssl

from services.egress_guard import _reraise_if_egress_blocked
from services.egress_guard import imap_egress_enabled

_log = logging.getLogger(__name__)

#: Bounded — a hung relay must not pin a worker indefinitely.
SEND_TIMEOUT_SECONDS = 30.0

#: Implicit-TLS submission port. Everything else (587, 25) uses STARTTLS.
IMPLICIT_TLS_PORT = 465


def connect_smtp(host: str, port: int, *, timeout: float = SEND_TIMEOUT_SECONDS):
    """Open an SMTP connection with port-driven, **verified** TLS.

    465 gets implicit TLS; everything else connects plain and issues STARTTLS.
    Both paths pass a verifying ``SSLContext``, so certificates **and** hostnames
    are checked.

    **One implementation, deliberately** (LUM-65 P2 #11, Step 4g). Both
    ``adapters/system_smtp.py`` and ``adapters/email_smtp_sender.py`` carried
    byte-identical copies of this — along with their own ``30.0`` timeout and
    ``465`` constants — and that duplication is precisely why the CWE-295 defect
    had to be fixed twice: `SMTP_SSL(...)` and `starttls()` were called with no
    ``context=`` in *both*, meaning ``CERT_NONE`` and no hostname check on
    channels carrying relay and mailbox credentials. Fixing one and missing the
    other would have left the vulnerability in place while looking addressed.
    The two senders stay separate — they differ in identity, error contracts and
    caller — but the socket mechanics are the same in every deployment and now
    live in one place.

    Lives here rather than in a shared adapter because this module already owns
    SMTP transport concerns (``smtp_egress_scope`` wraps exactly this connect),
    and because an adapter importing another adapter is the coupling the
    architecture rules exist to prevent.
    """
    context = ssl.create_default_context()
    if int(port) == IMPLICIT_TLS_PORT:
        return smtplib.SMTP_SSL(host, int(port), timeout=timeout, context=context)
    smtp = smtplib.SMTP(host, int(port), timeout=timeout)
    smtp.starttls(context=context)
    return smtp


@contextlib.contextmanager
def smtp_egress_scope(host: str, *, user_id: str | None = None):
    """Scope one SMTP submission connect so the only permitted egress host is
    ``host`` (the provider's submission endpoint).

    Fail-safe: flag off / ``tethered`` absent ⇒ send proceeds (the credential
    layer's TLS + SSRF guards still apply). A tethered block is translated into
    :class:`~services.egress_guard.EgressBlockedError` so the adapter can map it
    to a permanent (dead-letter) failure.
    """
    if not imap_egress_enabled():
        yield
        return
    try:
        import tethered
    except Exception:  # tethered absent — fail-safe to functional
        _log.error(
            "smtp.egress.guard_unavailable — tethered import failed; "
            "SMTP egress falls back to the credential-layer guards only",
            extra={"event": "smtp.egress.guard_unavailable"},
        )
        yield
        return
    try:
        with tethered.scope(allow=[host], allow_localhost=False, label="smtp_egress_scope"):
            yield
    except Exception as exc:
        _reraise_if_egress_blocked(exc, user_id=user_id)
        raise


def declared_smtp_hosts(user_id: str) -> list[tuple[str, int]]:
    """``(host, port)`` for every mailbox ``user_id`` has enabled SMTP send on.

    Best-effort and DNS-free (mirrors ``email_credentials.declared_imap_hosts``):
    one misconfigured mailbox must never sink the ceiling union. Resolution goes
    through :func:`email_credentials.resolve_smtp_host_port`, which honours the
    reuse-IMAP default (host derived from the IMAP mailbox's provider registry).
    """
    from connectors.registry import SMTP

    from services import email_credentials

    out: list[tuple[str, int]] = []
    for rec in email_credentials.mailbox_records(user_id, SMTP):
        try:
            hp = email_credentials.resolve_smtp_host_port(user_id, instance=rec.instance)
        except Exception as exc:  # noqa: BLE001 - one bad mailbox must not sink the union
            _log.warning(
                "smtp.egress.acl.enumerate_skip user_id=%s instance=%s reason=%s",
                user_id,
                rec.instance,
                type(exc).__name__,
            )
            continue
        if hp is not None:
            out.append(hp)
    return out


def household_smtp_hosts() -> list[tuple[str, int]]:
    """Union of every household member's declared SMTP ``(host, port)`` pairs.

    Used by the LUM-571 ceiling extras so the process-wide cap permits every
    member's submission host."""
    from services import users

    hosts: list[tuple[str, int]] = []
    for user in users.list_users():
        try:
            hosts.extend(declared_smtp_hosts(user.id))
        except Exception:  # noqa: BLE001 - one user's bad store must not sink the file
            _log.warning("smtp.egress.acl.enumerate_failed user_id=%s", user.id)
    return hosts


def verify_smtp_connection(
    *,
    host: str,
    port: int,
    username: str,
    app_password: str,
    user_id: str,
    auth_type: str = "app_password",
    access_token: str | None = None,
) -> tuple[bool, bool, str | None]:
    """Pre-save SMTP connect-test for the opt-in route.

    Keeps the concrete ``SmtpSender`` adapter import in the service layer (the
    email family constructs its adapters here — routes must not import ``adapters``;
    see ``test_routes_no_adapter_imports``). Never raises. Returns
    ``(ok_to_persist, tested, error_code)``:

    * ``(True, True, None)`` — connect + auth succeeded (persist, verified).
    * ``(True, False, None)`` — egress-blocked under containment: cannot verify, so
      the caller **soft-saves** with ``tested=false``.
    * ``(False, True, code)`` — a real auth/connect failure: the caller must **not**
      persist a broken opt-in, and surfaces ``code``.

    ``auth_type="xoauth2"`` + ``access_token`` (LUM-693) verifies an OAuth
    mailbox's SMTP send scope instead of an app password — used by the OAuth
    device-flow connect route as a best-effort, non-blocking check (SMTP reuses
    the already IMAP-verified token; a failure here is logged, not fatal).
    """
    from actions.errors import PermanentActionError
    from adapters.email_smtp_sender import SmtpSender

    try:
        SmtpSender().verify(
            host=host,
            port=port,
            username=username,
            app_password=app_password,
            user_id=user_id,
            auth_type=auth_type,
            access_token=access_token,
        )
        return (True, True, None)
    except PermanentActionError as exc:
        if exc.code == "egress_blocked":
            return (True, False, None)
        _log.warning("smtp.verify.failed code=%s", exc.code)
        return (False, True, exc.code)
