# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-811 — pre-main verification for LUM-810 email body read view.

Four obligations the LUM-810 plan marked *Required before merge* that need a
real IMAP stack and/or a real browser:

1. Live IMAP pass — cross-folder Message-ID locate, UIDVALIDITY mismatch
   recovery, oversized whole-message with text-only fetch (LUM-814).
2. p95 latency for ``fetch_body_bounded`` (target < 2.5 s on a bounded fetch).
3. 1 MiB adversarial ``html.parser`` input through ``extract_text``.
4. (Playwright is run separately by ``scripts/verify_lum_811.sh``.)

Uses Greenmail when ``LUM811_IMAP_HOST`` is unset (``docker run
greenmail/standalone`` on ports 3025/3143). Set ``LUM811_IMAP_HOST``,
``LUM811_IMAP_PORT``, ``LUM811_IMAP_USERNAME``, ``LUM811_IMAP_PASSWORD`` to
exercise a real provider mailbox instead.

Skips when Greenmail / the configured host is unreachable unless
``LUM811_REQUIRE=1`` (CI / ``make verify-lum-811``).
"""

from __future__ import annotations

import os
import smtplib
import socket
import statistics
import subprocess
import time
import uuid
from email.message import EmailMessage
from unittest.mock import patch

import pytest
from adapters.email_imap_source import _MAX_BODY_BYTES
from adapters.email_imap_source import EmailImapSource
from models.signals import SourceConfig
from services.email_body import _MAX_PARSE_BYTES
from services.email_body import extract_text
from tests.integration._pgguard import integration_db_required

from services import email_credentials

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_REQUIRE = os.environ.get("LUM811_REQUIRE", "") == "1"
_P95_TARGET_MS = 2500.0
_PARSE_BUDGET_S = 2.5


def _imap_host() -> str:
    return os.environ.get("LUM811_IMAP_HOST", "172.17.0.2").strip()


def _imap_port() -> int:
    return int(os.environ.get("LUM811_IMAP_PORT", "3143"))


def _imap_user() -> str:
    return os.environ.get("LUM811_IMAP_USERNAME", "user@localhost")


def _imap_password() -> str:
    return os.environ.get("LUM811_IMAP_PASSWORD", "user")


def _smtp_host() -> str:
    return os.environ.get("LUM811_SMTP_HOST", _imap_host())


def _smtp_port() -> int:
    return int(os.environ.get("LUM811_SMTP_PORT", "3025"))


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _require_or_skip(reason: str) -> None:
    # CI's lint-and-test runs a Greenmail service and sets LUM811_IMAP_HOST but not
    # LUM811_REQUIRE (that also unlocks the timing and image gates). A host that
    # was configured on purpose and is unreachable under the integration lane's
    # REQUIRE flag is a broken lane, not an absent fixture (LUM-720 / LUM-718).
    configured = bool(os.environ.get("LUM811_IMAP_HOST", "").strip())
    if _REQUIRE or (configured and integration_db_required()):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def lum811_mailbox():
    host = _imap_host()
    port = _imap_port()
    if not _reachable(host, port):
        _require_or_skip(f"LUM-811 IMAP not reachable at {host}:{port}")

    tag = uuid.uuid4().hex[:8]
    inbox_id = f"<lum811-inbox-{tag}@lumogis.test>"
    archive_id = f"<lum811-archive-{tag}@lumogis.test>"
    big_id = f"<lum811-big-{tag}@lumogis.test>"
    oversize_id = f"<lum811-oversize-{tag}@lumogis.test>"

    # INBOX: normal read + p95 target
    msg_inbox = EmailMessage()
    msg_inbox["From"] = "sender@example.com"
    msg_inbox["To"] = _imap_user()
    msg_inbox["Subject"] = f"lum811-inbox-{tag}"
    msg_inbox["Message-ID"] = inbox_id
    msg_inbox.set_content("Hello from INBOX — LUM-811 live pass.")

    # Archive: cross-folder locate (stale uid + wrong uidvalidity on purpose)
    msg_archive = EmailMessage()
    msg_archive["To"] = _imap_user()
    msg_archive["From"] = "sender@example.com"
    msg_archive["Subject"] = f"lum811-archive-{tag}"
    msg_archive["Message-ID"] = archive_id
    msg_archive.set_content("Body in Archive folder — organized mail still readable.")

    # LUM-814: short HTML + attachment pushing whole message over 1 MiB
    msg_big = EmailMessage()
    msg_big["From"] = "sender@example.com"
    msg_big["To"] = _imap_user()
    msg_big["Subject"] = f"lum811-big-{tag}"
    msg_big["Message-ID"] = big_id
    msg_big.set_content("<p>Short body behind a big photo.</p>", subtype="html")
    msg_big.add_attachment(
        b"\xff\xd8" + b"X" * 1_100_000,
        maintype="image",
        subtype="jpeg",
        filename="big.jpg",
    )

    # Whole message over cap with no separable text-only escape → too_large
    msg_oversize = EmailMessage()
    msg_oversize["From"] = "sender@example.com"
    msg_oversize["To"] = _imap_user()
    msg_oversize["Subject"] = f"lum811-oversize-{tag}"
    msg_oversize["Message-ID"] = oversize_id
    msg_oversize.set_content("x" * (_MAX_BODY_BYTES + 50_000))

    if _reachable(_smtp_host(), _smtp_port()):
        with smtplib.SMTP(_smtp_host(), _smtp_port(), timeout=30) as smtp:
            smtp.send_message(msg_inbox)

    import imapclient

    client = imapclient.IMAPClient(host, port=port, ssl=False, timeout=60)
    client.login(_imap_user(), _imap_password())
    archive = f"Lum811Archive{tag}"
    try:
        client.create_folder(archive)
    except imapclient.exceptions.IMAPClientError:
        pass
    client.append(archive, msg_archive.as_bytes())
    client.append("INBOX", msg_big.as_bytes())
    client.append("INBOX", msg_oversize.as_bytes())
    client.logout()

    conn = email_credentials.ImapConnection(
        host=host,
        port=port,
        username=_imap_user(),
        use_ssl=False,
        auth_type="app_password",
        password=_imap_password(),
    )
    source = SourceConfig(
        id=f"lum811-{tag}",
        name="LUM-811 verify",
        source_type="imap",
        url=f"imap://{host}:{port}/personal",
        category="",
        active=True,
        poll_interval=900,
        extraction_method="imap_full_ingest",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="lum811-user",
    )

    yield {
        "conn": conn,
        "source": source,
        "archive_folder": archive,
        "inbox_id": inbox_id,
        "archive_id": archive_id,
        "big_id": big_id,
        "oversize_id": oversize_id,
    }


def _adapter(mailbox) -> EmailImapSource:
    import imapclient

    def _client_factory(conn: email_credentials.ImapConnection):
        client = imapclient.IMAPClient(conn.host, port=conn.port, ssl=conn.use_ssl, timeout=30)
        client.login(conn.username, conn.password or "")
        return client

    return EmailImapSource(mailbox["source"], client_factory=_client_factory)


@pytest.fixture
def adapter(lum811_mailbox):
    with (
        patch.object(email_credentials, "load_connection", return_value=lum811_mailbox["conn"]),
        patch.object(email_credentials, "revalidate_imap_host", lambda *a, **k: None),
        patch("services.egress_guard.imap_egress_enabled", return_value=False),
    ):
        yield _adapter(lum811_mailbox)


def test_lum811_live_inbox_body(adapter, lum811_mailbox):
    out = adapter.fetch_body_bounded(
        uid=1,
        message_id=lum811_mailbox["inbox_id"],
        uidvalidity=0,
    )
    assert out.reason == "ok"
    assert "Hello from INBOX" in out.text


def test_lum811_live_archive_cross_folder(adapter, lum811_mailbox):
    """Message-ID locate beyond INBOX — the organize / filed-mail case."""
    out = adapter.fetch_body_bounded(
        uid=99_999,
        message_id=lum811_mailbox["archive_id"],
        folder_hint=lum811_mailbox["archive_folder"],
        uidvalidity=1,
    )
    assert out.reason == "ok"
    assert "Archive folder" in out.text


def test_lum811_live_uidvalidity_mismatch_recovered_by_message_id(adapter, lum811_mailbox):
    """Stale uid + wrong UIDVALIDITY must not report not_found when Message-ID hits."""
    out = adapter.fetch_body_bounded(
        uid=1,
        message_id=lum811_mailbox["inbox_id"],
        uidvalidity=9_999_999,
    )
    assert out.reason == "ok"
    assert out.text


def test_lum811_live_oversized_message_text_only_fetch(adapter, lum811_mailbox):
    """LUM-814: attachment-heavy mail still yields readable text."""
    out = adapter.fetch_body_bounded(
        uid=1,
        message_id=lum811_mailbox["big_id"],
        uidvalidity=0,
    )
    assert out.reason == "ok"
    assert "Short body behind a big photo" in out.text
    assert "<p>" not in out.text


def test_lum811_live_whole_message_over_cap_refused(adapter, lum811_mailbox):
    out = adapter.fetch_body_bounded(
        uid=1,
        message_id=lum811_mailbox["oversize_id"],
        uidvalidity=0,
    )
    assert out.reason == "too_large"
    assert out.text == ""


def test_lum811_p95_bounded_fetch_under_target(adapter, lum811_mailbox):
    samples_ms: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        out = adapter.fetch_body_bounded(
            uid=1,
            message_id=lum811_mailbox["inbox_id"],
            uidvalidity=0,
        )
        samples_ms.append((time.perf_counter() - start) * 1000.0)
        assert out.reason == "ok"
    p95 = statistics.quantiles(samples_ms, n=100)[94]
    print(f"\nLUM-811 p95 fetch_body_bounded: {p95:.1f}ms (target < {_P95_TARGET_MS}ms; n=30)")
    assert p95 < _P95_TARGET_MS, (
        f"p95 {p95:.1f}ms exceeds {_P95_TARGET_MS}ms target "
        f"(min={min(samples_ms):.1f} max={max(samples_ms):.1f})"
    )


def test_lum811_adversarial_parse_benchmark():
    # Gated on _REQUIRE like the rest of this harness, for two reasons.
    #
    # It needs no backend, so in `make test`'s hermeticity lane
    # (`LUMOGIS_ASSERT_NO_HERMETIC_INTEGRATION=1`, every service pointed at a
    # dead port) it was the one test that PASSED — which is exactly what that
    # gate exists to catch. `orchestrator/tests/integration/` is for tests
    # requiring real external services (LUM-753 / ADR 194); a pure-CPU
    # benchmark passing there is a taxonomy violation, and it failed `make test`
    # at `check-integration-hermeticity`.
    #
    # And it is a *timing* assertion. Run on an arbitrary machine under
    # arbitrary load it measures the load, not the parser — the same mistake
    # that produced non-monotonic ALTCHA numbers earlier in this arc. It
    # belongs in the deliberate `make verify-lum-811` lane, where the operator
    # has chosen to measure.
    if not _REQUIRE:
        pytest.skip(
            "parse benchmark is a timing assertion for the deliberate lane; "
            "set LUM811_REQUIRE=1 (make verify-lum-811)"
        )

    """CVE-2025-6069 class: 1 MiB pathological HTML must stay bounded in time."""
    worst = 0.0
    for size in (_MAX_PARSE_BYTES, _MAX_PARSE_BYTES * 4, _MAX_PARSE_BYTES * 16):
        msg = EmailMessage()
        msg.set_content("<" * size, subtype="html")
        start = time.perf_counter()
        out = extract_text(msg.as_bytes())
        elapsed = time.perf_counter() - start
        worst = max(worst, elapsed)
        assert isinstance(out, str)

    assert worst < _PARSE_BUDGET_S, (
        f"adversarial parse took {worst:.2f}s (budget {_PARSE_BUDGET_S}s)"
    )
    print(f"\nLUM-811 adversarial parse worst case: {worst:.2f}s (budget {_PARSE_BUDGET_S}s)")


def test_lum811_python_patch_at_least_3_12_11():
    """The *deployed* interpreter carries the html.parser fix LUM-810 relies on.

    Gated on ``LUM811_REQUIRE`` like the rest of this module, because the claim
    is about the runtime that actually parses untrusted mail — the orchestrator
    image, which ``make verify-lum-811`` runs this inside. Asserted ungated it
    instead measured whatever interpreter invoked pytest, so a contributor whose
    venv is 3.12.3 got a red ``make test`` describing a risk their deployment
    does not carry, while a green run on a laptop proved nothing about the image.
    """
    import sys

    # Gated directly on _REQUIRE rather than through `_require_or_skip`: that
    # helper *fails* when the flag is set, because it reports a precondition the
    # run promised and did not meet. Here the flag means the opposite — we are
    # finally in the environment where the assertion is meaningful.
    if not _REQUIRE:
        pytest.skip(
            "python-version floor is a property of the deployed image; set "
            "LUM811_REQUIRE=1 (make verify-lum-811 runs this in the container)"
        )
    major, minor, patch = sys.version_info[:3]
    assert (major, minor, patch) >= (3, 12, 11), sys.version


def _greenmail_container_id() -> str | None:
    try:
        out = subprocess.run(
            ["docker", "ps", "-q", "-f", "name=lum811-greenmail"],
            capture_output=True,
            text=True,
            check=False,
        )
        cid = out.stdout.strip().splitlines()
        return cid[0] if cid else None
    except OSError:
        return None


def test_lum811_greenmail_or_real_host_documented(lum811_mailbox):
    """Record which IMAP backend this run used (for Linear closure evidence)."""
    cid = _greenmail_container_id()
    backend = "greenmail" if cid else f"custom:{_imap_host()}"
    # Always passes — the assertion is that the fixture reached this point.
    assert lum811_mailbox["inbox_id"]
    print(f"\nLUM-811 IMAP backend: {backend} host={_imap_host()}:{_imap_port()}")
