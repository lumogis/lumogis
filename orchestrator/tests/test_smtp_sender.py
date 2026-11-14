# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Adapter unit tests for the SMTP submission sender (LUM-650).

Driven by a fake transport that records login / send_message / quit — no real
SMTP server. Covers the shared MIME builder (Message-ID + headers +
threading-ready in_reply_to/references), port-driven TLS selection (465 implicit
vs 587 STARTTLS), header-injection rejection, the partial/total recipient-refusal
contract, and the egress-block → PermanentActionError mapping.
"""

from __future__ import annotations

import smtplib

import pytest
from actions.errors import PermanentActionError
from adapters.email_smtp_sender import SmtpSender
from services.egress_guard import EgressBlockedError


class FakeSMTP:
    """Records what the adapter does; ``refused`` is what send_message returns."""

    def __init__(self, host, port, timeout=None, *, kind="plain", refused=None, raise_on=None):
        self.host = host
        self.port = port
        self.kind = kind
        self._refused = refused or {}
        self._raise_on = raise_on
        self.calls: list = []
        # XOAUTH2 path: track whether an EHLO is in effect (a real server 503s an
        # AUTH issued with no post-connect/post-TLS EHLO — the LUM-686 P1 gotcha).
        self.ehloed = False
        self.auth_arg = None

    def starttls(self, *, context=None):
        # `context` is recorded, not ignored: the adapter must pass a verifying
        # SSLContext (LUM-65 P2 #11), and a fake that swallowed the argument
        # would let a regression to unverified TLS pass silently.
        self.calls.append(("starttls", context))
        self.tls_context = context
        self.ehloed = False  # STARTTLS resets EHLO state (RFC 3207 §4.2)

    def ehlo_or_helo_if_needed(self):
        self.calls.append(("ehlo",))
        self.ehloed = True

    def docmd(self, cmd, arg=None):
        self.calls.append(("docmd", cmd))
        if cmd == "AUTH":
            self.auth_arg = arg
            if not self.ehloed:
                return (503, b"5.5.1 EHLO/HELO first")  # what a real server answers
            if self._raise_on == "xoauth2_reject":
                return (535, b"5.7.8 bad token")
            return (235, b"2.7.0 Accepted")
        return (250, b"ok")

    def login(self, user, pw):
        self.calls.append(("login", user))
        if self._raise_on == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad creds")

    def send_message(self, msg):
        self.calls.append(("send_message", msg["To"]))
        if self._raise_on == "all_refused":
            raise smtplib.SMTPRecipientsRefused({"z@x.com": (550, b"no")})
        return dict(self._refused)

    def quit(self):
        self.calls.append(("quit",))


# --------------------------------------------------------------------------- #
# build_message — the shared MIME builder
# --------------------------------------------------------------------------- #
def test_build_message_sets_message_id_and_headers():
    msg = SmtpSender.build_message(
        from_addr="alice@gmail.com",
        to=["bob@x.com", "carol@y.com"],
        subject="Hi there",
        body="hello\nworld",
    )
    assert msg["From"] == "alice@gmail.com"
    assert msg["To"] == "bob@x.com, carol@y.com"
    assert msg["Subject"] == "Hi there"
    assert msg["Message-ID"] and msg["Message-ID"].endswith("@gmail.com>")
    assert "In-Reply-To" not in msg
    assert msg.get_content().strip() == "hello\nworld"


def test_build_message_threading_ready():
    msg = SmtpSender.build_message(
        from_addr="a@x.com",
        to=["b@x.com"],
        subject="Re: t",
        body="x",
        in_reply_to="<orig@x.com>",
        references="<a@x.com> <b@x.com>",
    )
    assert msg["In-Reply-To"] == "<orig@x.com>"
    assert msg["References"] == "<a@x.com> <b@x.com>"


def test_build_message_no_recipients_rejected():
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender.build_message(from_addr="a@x.com", to=[], subject="s", body="b")
    assert ei.value.code == "no_recipients"


@pytest.mark.parametrize("field_value", ["bad\r\nBcc: evil@x.com", "line1\nline2"])
def test_header_injection_rejected_subject(field_value):
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender.build_message(
            from_addr="a@x.com", to=["b@x.com"], subject=field_value, body="ok"
        )
    assert ei.value.code == "header_injection"


def test_header_injection_rejected_recipient():
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender.build_message(
            from_addr="a@x.com", to=["b@x.com\r\nRcpt: evil@x.com"], subject="s", body="b"
        )
    assert ei.value.code == "header_injection"


# --------------------------------------------------------------------------- #
# send — port-driven TLS + refusal + egress
# --------------------------------------------------------------------------- #
def _patch_smtplib(monkeypatch, **fake_kwargs):
    """Patch the module's smtplib so SMTP_SSL/SMTP construct a recording fake."""
    created: dict = {}

    def _ssl(host, port, timeout=None, context=None):
        obj = FakeSMTP(host, port, timeout, kind="ssl", **fake_kwargs)
        obj.tls_context = context  # asserted by the implicit-TLS test
        created["obj"] = obj
        return created["obj"]

    def _plain(host, port, timeout=None):
        created["obj"] = FakeSMTP(host, port, timeout, kind="plain", **fake_kwargs)
        return created["obj"]

    # Patched where the socket is actually opened. `_connect` delegates to
    # `services.smtp_transport.connect_smtp` (Step 4g — the two adapters had
    # byte-identical copies, which is why the TLS defect shipped twice), so
    # patching the adapter module's smtplib would no longer intercept.
    monkeypatch.setattr("services.smtp_transport.smtplib.SMTP_SSL", _ssl)
    monkeypatch.setattr("services.smtp_transport.smtplib.SMTP", _plain)
    return created


def test_port_465_uses_implicit_tls(monkeypatch):
    created = _patch_smtplib(monkeypatch)
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    SmtpSender().send(msg, host="smtp.x.com", port=465, username="a@x.com", app_password="pw")
    obj = created["obj"]
    assert obj.kind == "ssl"
    assert not any(c[0] == "starttls" for c in obj.calls)  # implicit TLS on 465
    _assert_verifying_context(obj.tls_context)


def test_port_587_uses_starttls(monkeypatch):
    created = _patch_smtplib(monkeypatch)
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    result = SmtpSender().send(
        msg, host="smtp.x.com", port=587, username="a@x.com", app_password="pw"
    )
    obj = created["obj"]
    assert obj.kind == "plain"
    assert any(c[0] == "starttls" for c in obj.calls)
    _assert_verifying_context(obj.tls_context)
    assert result.message_id == msg["Message-ID"]
    assert result.refused == ()


def _assert_verifying_context(context) -> None:
    """Both TLS paths must verify the certificate AND the hostname.

    LUM-65 P2 #11: `SMTP_SSL(...)` and `starttls()` were called with no
    `context=`, which is `CERT_NONE` and no hostname check (CWE-295) on a
    channel carrying a mailbox password. Asserting the context *exists* would
    not be enough — an `ssl._create_unverified_context()` is also a context.
    """
    import ssl

    assert context is not None, "no SSLContext passed — TLS would be unverified"
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_message_carries_a_date_header(monkeypatch):
    """RFC 5322 §3.6 mandates it and `smtplib.send_message` does not add one.

    Absent, relays that do not rewrite headers score it MISSING_DATE, which is
    how a password-reset mail lands in junk (LUM-65 P1 #9).
    """
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")

    assert msg["Date"], "no Date header"
    from email.utils import parsedate_to_datetime

    assert parsedate_to_datetime(msg["Date"]) is not None, "Date is not RFC-parseable"


def test_partial_recipient_refusal_surfaces_refused_set(monkeypatch):
    # A PARTIAL refusal does NOT raise in the adapter — the accepted set received
    # the message; SendResult.refused names the rejected ones so the handler logs
    # the accepted set and then surfaces the failure.
    _patch_smtplib(monkeypatch, refused={"bad@x.com": (550, b"no")})
    msg = SmtpSender.build_message(
        from_addr="a@x.com", to=["ok@x.com", "bad@x.com"], subject="s", body="b"
    )
    result = SmtpSender().send(
        msg, host="smtp.x.com", port=587, username="a@x.com", app_password="pw"
    )
    assert result.refused == ("bad@x.com",)


def test_all_recipients_refused_is_permanent(monkeypatch):
    _patch_smtplib(monkeypatch, raise_on="all_refused")
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["z@x.com"], subject="s", body="b")
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender().send(msg, host="smtp.x.com", port=587, username="a@x.com", app_password="pw")
    assert ei.value.code == "recipients_refused"


def test_auth_failure_is_permanent(monkeypatch):
    _patch_smtplib(monkeypatch, raise_on="auth")
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender().send(msg, host="smtp.x.com", port=587, username="a@x.com", app_password="pw")
    assert ei.value.code == "smtp_auth"


def test_egress_block_is_permanent():
    def _factory(host, port):
        raise EgressBlockedError(host=host, port=port)

    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender(transport_factory=_factory).send(
            msg, host="blocked.x.com", port=587, username="a@x.com", app_password="pw"
        )
    assert ei.value.code == "egress_blocked"


def test_verify_maps_auth_failure(monkeypatch):
    _patch_smtplib(monkeypatch, raise_on="auth")
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender().verify(host="smtp.x.com", port=587, username="a@x.com", app_password="bad")
    assert ei.value.code == "smtp_auth"


# --------------------------------------------------------------------------- #
# services.smtp_transport.verify_smtp_connection — the never-raises wrapper
# (LUM-693: forwards auth_type/access_token so an xoauth2 caller, e.g. the
# OAuth device-flow poll route, can verify SMTP without an app password).
# --------------------------------------------------------------------------- #
def test_verify_smtp_connection_forwards_xoauth2_args(monkeypatch):
    from services import smtp_transport

    captured = {}

    class _FakeSender:
        def verify(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("adapters.email_smtp_sender.SmtpSender", _FakeSender)
    ok, tested, code = smtp_transport.verify_smtp_connection(
        host="smtp.office365.com",
        port=587,
        username="a@outlook.com",
        app_password="",
        user_id="u1",
        auth_type="xoauth2",
        access_token="TOK",
    )
    assert (ok, tested, code) == (True, True, None)
    assert captured["auth_type"] == "xoauth2" and captured["access_token"] == "TOK"


def test_verify_smtp_connection_xoauth2_auth_failure_maps_to_smtp_auth(monkeypatch):
    from services import smtp_transport

    _patch_smtplib(monkeypatch, raise_on="xoauth2_reject")
    ok, tested, code = smtp_transport.verify_smtp_connection(
        host="smtp.office365.com",
        port=587,
        username="a@outlook.com",
        app_password="",
        user_id="u1",
        auth_type="xoauth2",
        access_token="TOK",
    )
    assert (ok, tested, code) == (False, True, "smtp_auth")


# --------------------------------------------------------------------------- #
# send — xoauth2 (LUM-686): AUTH XOAUTH2 with a mandatory pre-AUTH EHLO
# --------------------------------------------------------------------------- #
def test_xoauth2_send_issues_ehlo_before_auth(monkeypatch):
    # The P1 regression guard: the fake 503s an AUTH with no EHLO in effect, so a
    # successful send PROVES ehlo_or_helo_if_needed() ran before the AUTH docmd.
    created = _patch_smtplib(monkeypatch)
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    result = SmtpSender().send(
        msg,
        host="smtp.gmail.com",
        port=587,
        username="a@x.com",
        app_password="",
        auth_type="xoauth2",
        access_token="TOK",
    )
    obj = created["obj"]
    assert ("ehlo",) in obj.calls and ("docmd", "AUTH") in obj.calls
    assert obj.calls.index(("ehlo",)) < obj.calls.index(("docmd", "AUTH"))
    # login() is never used on the xoauth2 path.
    assert not any(c[0] == "login" for c in obj.calls)
    assert result.message_id == msg["Message-ID"]


def test_xoauth2_send_sends_correct_sasl_string(monkeypatch):
    import base64

    created = _patch_smtplib(monkeypatch)
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    SmtpSender().send(
        msg,
        host="smtp.gmail.com",
        port=587,
        username="alice@x.com",
        app_password="",
        auth_type="xoauth2",
        access_token="TOK",
    )
    arg = created["obj"].auth_arg
    assert arg.startswith("XOAUTH2 ")
    assert base64.b64decode(arg.split(" ", 1)[1]) == b"user=alice@x.com\x01auth=Bearer TOK\x01\x01"


def test_xoauth2_non_235_is_permanent(monkeypatch):
    _patch_smtplib(monkeypatch, raise_on="xoauth2_reject")
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender().send(
            msg,
            host="smtp.gmail.com",
            port=587,
            username="a@x.com",
            app_password="",
            auth_type="xoauth2",
            access_token="TOK",
        )
    assert ei.value.code == "smtp_auth"


def test_xoauth2_missing_token_is_permanent(monkeypatch):
    _patch_smtplib(monkeypatch)
    msg = SmtpSender.build_message(from_addr="a@x.com", to=["b@x.com"], subject="s", body="b")
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender().send(
            msg,
            host="smtp.gmail.com",
            port=587,
            username="a@x.com",
            app_password="",
            auth_type="xoauth2",
            access_token=None,
        )
    assert ei.value.code == "smtp_auth"


# --------------------------------------------------------------------------- #
# verify — xoauth2 (LUM-693): the pre-save/pre-persist connect-test reuses the
# SAME _authenticate() helper send() uses, so it gets the mandatory pre-AUTH
# EHLO + SASL string + non-235 handling for free (no duplicated auth logic).
# --------------------------------------------------------------------------- #
def test_verify_xoauth2_issues_ehlo_before_auth(monkeypatch):
    created = _patch_smtplib(monkeypatch)
    SmtpSender().verify(
        host="smtp.office365.com",
        port=587,
        username="a@outlook.com",
        app_password="",
        auth_type="xoauth2",
        access_token="TOK",
    )
    obj = created["obj"]
    assert ("ehlo",) in obj.calls and ("docmd", "AUTH") in obj.calls
    assert obj.calls.index(("ehlo",)) < obj.calls.index(("docmd", "AUTH"))
    assert not any(c[0] == "login" for c in obj.calls)  # login() never used


def test_verify_xoauth2_non_235_is_permanent(monkeypatch):
    _patch_smtplib(monkeypatch, raise_on="xoauth2_reject")
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender().verify(
            host="smtp.office365.com",
            port=587,
            username="a@outlook.com",
            app_password="",
            auth_type="xoauth2",
            access_token="TOK",
        )
    assert ei.value.code == "smtp_auth"


def test_verify_xoauth2_missing_token_is_permanent(monkeypatch):
    _patch_smtplib(monkeypatch)
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender().verify(
            host="smtp.office365.com",
            port=587,
            username="a@outlook.com",
            app_password="",
            auth_type="xoauth2",
            access_token=None,
        )
    assert ei.value.code == "smtp_auth"


def test_verify_app_password_path_unchanged(monkeypatch):
    # Regression: the default auth_type keeps calling smtp.login (not AUTH XOAUTH2).
    created = _patch_smtplib(monkeypatch)
    SmtpSender().verify(host="smtp.x.com", port=587, username="a@x.com", app_password="pw")
    obj = created["obj"]
    assert ("login", "a@x.com") in obj.calls
    assert not any(c[0] == "docmd" for c in obj.calls)


# --------------------------------------------------------------------------- #
# LUM-698 — CC/BCC on the shared MIME builder + the BCC non-disclosure contract
# --------------------------------------------------------------------------- #
def test_build_message_sets_cc_and_bcc_headers():
    msg = SmtpSender.build_message(
        from_addr="alice@gmail.com",
        to=["bob@x.com"],
        subject="Hi",
        body="hello",
        cc=["carol@y.com", "dan@y.com"],
        bcc=["eve@z.com"],
    )
    assert msg["To"] == "bob@x.com"
    assert msg["Cc"] == "carol@y.com, dan@y.com"
    assert msg["Bcc"] == "eve@z.com"


def test_build_message_omits_cc_bcc_headers_when_empty():
    msg = SmtpSender.build_message(
        from_addr="alice@gmail.com", to=["bob@x.com"], subject="Hi", body="hello"
    )
    assert msg["Cc"] is None
    assert msg["Bcc"] is None


def test_build_message_bcc_only_is_valid():
    """A message addressed ONLY via BCC is legitimate — the no_recipients guard
    is on the union, not on `to` alone."""
    msg = SmtpSender.build_message(
        from_addr="alice@gmail.com", to=[], subject="Hi", body="hello", bcc=["eve@z.com"]
    )
    assert msg["To"] is None
    assert msg["Bcc"] == "eve@z.com"


def test_build_message_no_recipients_anywhere_raises():
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender.build_message(from_addr="a@x.com", to=[], subject="s", body="b")
    assert ei.value.code == "no_recipients"


@pytest.mark.parametrize("field", ["cc", "bcc"])
def test_build_message_rejects_control_chars_in_cc_bcc(field):
    kwargs = {"cc": [], "bcc": []}
    kwargs[field] = ["evil@x.com\r\nBcc: attacker@evil.com"]
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender.build_message(
            from_addr="a@x.com", to=["b@x.com"], subject="s", body="b", **kwargs
        )
    assert ei.value.code == "header_injection"


class _RealSendMessageTransport:
    """Fake transport that uses the **REAL** ``smtplib.SMTP.send_message``.

    The other fakes in this file stub ``send_message`` wholesale, which would
    make a BCC assertion vacuous (it would only prove our own fake's behaviour).
    Here only ``sendmail`` — the primitive the real ``send_message`` ultimately
    calls — is captured, so the Bcc handling under test is genuinely the
    stdlib's: envelope from To+Cc+Bcc, ``del msg_copy['Bcc']`` before flatten.
    """

    send_message = smtplib.SMTP.send_message  # the real implementation

    def __init__(self):
        self.envelope_to: list[str] = []
        self.flat: bytes = b""

    def ehlo_or_helo_if_needed(self):
        return None

    def has_extn(self, name):
        return False

    def sendmail(self, from_addr, to_addrs, flatmsg, mail_options=(), rcpt_options=()):
        self.envelope_to = list(to_addrs)
        self.flat = flatmsg
        return {}

    def login(self, user, pw):
        return None

    def quit(self):
        return None


def test_bcc_delivered_but_never_transmitted():
    """The definitive BCC non-disclosure test (LUM-698).

    Asserts BOTH halves against the real stdlib path: the BCC address IS in the
    envelope (so it is actually delivered) and the ``Bcc`` header is NOT in the
    transmitted bytes (so no other recipient can see it).
    """
    transport = _RealSendMessageTransport()
    msg = SmtpSender.build_message(
        from_addr="alice@gmail.com",
        to=["bob@x.com"],
        subject="Hi",
        body="hello",
        cc=["carol@y.com"],
        bcc=["eve@z.com"],
    )
    SmtpSender(transport_factory=lambda h, p: transport).send(
        msg, host="smtp.x.com", port=587, username="alice@gmail.com", app_password="pw"
    )

    # Delivered: every recipient class reaches the envelope.
    assert set(transport.envelope_to) == {"bob@x.com", "carol@y.com", "eve@z.com"}
    # Not disclosed: no Bcc header, and the address appears nowhere in the bytes.
    flat = transport.flat.decode("utf-8", "replace")
    assert "Bcc:" not in flat
    assert "eve@z.com" not in flat
    # Cc IS legitimately visible to all recipients.
    assert "Cc: carol@y.com" in flat
