# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""`GET /sources` exposes the attachment-filing opt-in (LUM-742).

`sources.attachment_ingest_enabled` has been written by
`PATCH /attachment-ingest` since LUM-702 and read back by **nothing**. That is
why `AttachmentIngestToggle` — whose `enabled` prop is required — could never
have a correct consumer: a parent had to guess `False` and would render "off"
for a mailbox that was on.

The watermark ships alongside it because the three activation outcomes are only
distinguishable from its presence. Absent means a first-ever enable starts from
now and nothing is pending until new mail arrives; present means a re-enable
resumes from the original opt-in and surfaces everything accumulated since.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes import signals as mod

import config

SOURCE = "33333333-3333-3333-3333-333333333333"


class _Store:
    """Routes ``fetch_all`` on the SQL text.

    The credential ``SELECT`` (LUM-777) goes through the REAL
    ``ccs.list_records`` -> ``_record_from_row`` path, so NULL handling is
    exercised for real. Without this routing the listing's
    ``assert "attachment_ingest_enabled" in sql`` fires on that SELECT and every
    IMAP listing test silently runs the degraded path.
    """

    def __init__(self, rows, cred_rows=(), cred_error=None):
        self.rows = rows
        self.cred_rows = cred_rows
        self.cred_error = cred_error
        self.cred_calls = []

    def fetch_all(self, sql, params=None):
        if "FROM user_connector_credentials" in sql:
            self.cred_calls.append((sql, params))
            if self.cred_error is not None:
                raise self.cred_error
            return list(self.cred_rows)
        assert "attachment_ingest_enabled" in sql, "listing must select the opt-in"
        assert "user_id = %s" in sql, "listing must stay user-scoped"
        return list(self.rows)


class _Scheduler:
    @staticmethod
    def get_jobs():
        return []


def _row(**over):
    row = {
        "id": SOURCE,
        "name": "work@example.com",
        "source_type": "imap",
        "url": "imap://mail.example.com",
        "category": "email",
        "active": True,
        "poll_interval": 900,
        "extraction_method": "imap_full_ingest",
        "last_polled_at": None,
        "last_signal_at": None,
        "last_poll_error_code": None,
        "last_poll_error_at": None,
        "attachment_ingest_enabled": False,
        "attachment_ingest_since": None,
    }
    row.update(over)
    return row


def _cred_row(connector, instance, keys):
    """A credential row with every ``_SELECT_RECORD_COLS`` column and NO ``ciphertext``."""
    when = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return {
        "user_id": "u1",
        "connector": connector,
        "instance": instance,
        "created_at": when,
        "updated_at": when,
        "created_by": "u1",
        "updated_by": "u1",
        "key_version": 1,
        "delivery_paused": False,
        "delivery_paused_reason": None,
        "delivery_paused_detail": None,
        "delivery_paused_at": None,
        "enabled": True,
        "configured_keys": keys,
    }


# Key names the real writers produce (device flow `signals.py` / setup form).
OAUTH_KEYS = [
    "access_token",
    "auth_type",
    "expiry",
    "host",
    "port",
    "provider",
    "refresh_token",
    "use_ssl",
    "username",
]
APP_PASSWORD_KEYS = ["auth_type", "host", "password", "port", "use_ssl", "username"]


def _client(rows, monkeypatch, *, full_ingest_on=True, cred_rows=(), cred_error=None):
    """Returns the client; the store is on ``client.store`` (``cred_calls``)."""
    from auth import UserContext

    store = _Store(rows, cred_rows=cred_rows, cred_error=cred_error)
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(config, "get_scheduler", lambda: _Scheduler())
    from signals import feed_monitor

    monkeypatch.setattr(feed_monitor, "_email_full_ingest_enabled", lambda: full_ingest_on)

    ctx = UserContext(user_id="u1", role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    client = TestClient(app)
    client.store = store
    return client


def test_listing_exposes_the_opt_in_defaulting_off(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        r = _client([_row()], monkeypatch).get("/sources")

    assert r.status_code == 200
    src = r.json()["sources"][0]
    assert src["attachment_ingest_enabled"] is False
    assert src["attachment_ingest_since"] is None
    assert src["auth_method"] is None
    # The IMAP row makes the route read credential metadata. With the harness
    # answering that SELECT, the non-degraded path must be what ran: otherwise
    # every listing test here would silently be hitting the `except`.
    assert not [r for r in caplog.records if "credential metadata" in r.getMessage()]


def test_listing_reports_an_enabled_mailbox_and_its_watermark(monkeypatch):
    when = datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    rows = [_row(attachment_ingest_enabled=True, attachment_ingest_since=when)]

    src = _client(rows, monkeypatch).get("/sources").json()["sources"][0]

    assert src["attachment_ingest_enabled"] is True
    assert src["attachment_ingest_since"] == when.isoformat()


def test_a_null_flag_reads_as_off_not_as_missing(monkeypatch):
    """An older row predating migration 067 must not render as an absent key.

    The toggle's prop is a required boolean; `None` would land as `null` in
    JSON and read as "unknown" on a control that has no unknown state.
    """
    src = (
        _client([_row(attachment_ingest_enabled=None)], monkeypatch)
        .get("/sources")
        .json()["sources"][0]
    )

    assert src["attachment_ingest_enabled"] is False


@pytest.mark.parametrize("flag", [True, False])
def test_opt_in_is_independent_of_the_operator_full_ingest_flag(monkeypatch, flag):
    """`full_ingest_active` and the filing opt-in answer different questions.

    A mailbox can be opted in while the operator flag is off — that is the
    "pending operator enable" state the activation panel has to explain rather
    than present as a working toggle.
    """
    rows = [_row(attachment_ingest_enabled=True)]

    src = _client(rows, monkeypatch, full_ingest_on=flag).get("/sources").json()["sources"][0]

    assert src["attachment_ingest_enabled"] is True
    assert src["full_ingest_active"] is flag


# --- LUM-771: `smtp_host_required` ---------------------------------------- #
#
# `set_email_smtp` answers `422 smtp_host_required` for a mailbox whose IMAP host
# has no provider-registry entry. Until now a client could only learn that by
# trying and failing, so it had to show submission host/port to everybody or send
# the user through an avoidable error. Derived on read from `sources.url` by the
# same `provider_for_host` the route matches with — never stored.


def test_smtp_host_required_is_true_only_for_an_unknown_imap_host(monkeypatch):
    """BOTH fixture URLs carry a port, deliberately.

    Every real `sources.url` does — `create_email_source` writes
    `imap://{host}:{port}/{label}` — and `.netloc` (the parse this file's
    neighbour in `signals.py` uses) includes it. A port-less fixture makes
    `.netloc` and `.hostname` agree, so this test would go green against an
    implementation that reports `true` for 100% of production rows.
    """
    rows = [
        _row(id="a" * 8, name="gmail", url="imap://imap.gmail.com:993/personal"),
        _row(id="b" * 8, name="self-hosted", url="imap://mail.selfhosted.example:993/inbox"),
    ]
    srcs = _client(rows, monkeypatch).get("/sources").json()["sources"]

    by_name = {s["name"]: s for s in srcs}
    assert by_name["gmail"]["smtp_host_required"] is False  # registry supplies smtp.gmail.com
    assert by_name["self-hosted"]["smtp_host_required"] is True


def test_an_unparseable_source_url_does_not_crash_the_listing(monkeypatch):
    """`urlsplit` RAISES on an unmatched "[" — it does not return None.

    The fixture must be a URL that actually raises. `"not-a-url"` parses fine
    (`.hostname` is None) and would pass against an unguarded implementation,
    which then 500s the ENTIRE listing for one bad row — and `EmailSourceRequest.
    host` is an unconstrained str interpolated verbatim into `sources.url`, so any
    user can create one.
    """
    from urllib.parse import urlsplit

    bad = "imap://[bad:993/x"
    with pytest.raises(ValueError):
        urlsplit(bad).hostname  # the fixture's premise, asserted rather than assumed

    rows = [
        _row(id="a" * 8, name="broken", url=bad),
        _row(id="b" * 8, name="gmail", url="imap://imap.gmail.com:993/personal"),
    ]
    r = _client(rows, monkeypatch).get("/sources")

    assert r.status_code == 200
    by_name = {s["name"]: s for s in r.json()["sources"]}
    assert len(by_name) == 2, "one bad row must not empty the page"
    assert by_name["broken"]["smtp_host_required"] is True  # unknown host ⇒ required
    assert by_name["gmail"]["smtp_host_required"] is False


def test_smtp_host_required_is_false_for_every_non_imap_source(monkeypatch):
    """The gate, which is load-bearing: `provider_for_host` is None for an RSS or
    Paperless host too, so an ungated version publishes `true` for every
    non-mailbox row — a flag about SMTP submission on a web bookmark."""
    rows = [
        _row(id="a" * 8, name="feed", source_type="rss", url="https://example.com/feed.xml"),
        _row(id="b" * 8, name="page", source_type="web", url="https://example.com/news"),
        _row(id="c" * 8, name="docs", source_type="paperless", url="http://paperless:8000"),
    ]
    srcs = _client(rows, monkeypatch).get("/sources").json()["sources"]

    assert [s["smtp_host_required"] for s in srcs] == [False, False, False]


# --- LUM-777: `auth_method` ------------------------------------------------ #
#
# Derived on read from the plaintext `configured_keys` of the IMAP credential
# row, one metadata SELECT per request, never decrypted. `null` always means
# UNKNOWN (ADR-219): the client then keeps its hedged copy. The fixture key
# lists come from the real writers (OAUTH_KEYS / APP_PASSWORD_KEYS above); both
# carry `auth_type`, which is what lets a discriminate-on-`auth_type`
# implementation fail the first case as well as the third.

A, B, C, D, E = ("a" * 8, "b" * 8, "c" * 8, "d" * 8, "e" * 8)


def _imap(id_, name):
    return _row(id=id_, name=name, url="imap://imap.gmail.com:993/inbox")


def test_auth_method_is_derived_per_mailbox_from_configured_keys(monkeypatch):
    rows = [
        _imap(A, "oauth"),
        _imap(B, "app"),
        _imap(C, "pre079"),
        _imap(D, "no-record"),
        _row(id=E, name="feed", source_type="rss", url="https://example.com/feed.xml"),
    ]
    cred_rows = [
        _cred_row("imap", A, OAUTH_KEYS),
        _cred_row("imap", B, APP_PASSWORD_KEYS),
        _cred_row("imap", C, None),
        _cred_row("smtp", A, ["reuse_imap"]),
        # A historical record for a connector that no longer exists: changes nothing.
        _cred_row("retired-connector", A, ["refresh_token"]),
    ]

    srcs = _client(rows, monkeypatch, cred_rows=cred_rows).get("/sources").json()["sources"]

    by_name = {s["name"]: s["auth_method"] for s in srcs}
    assert by_name == {
        "oauth": "oauth",
        "app": "app_password",
        "pre079": None,
        "no-record": None,
        "feed": None,
    }


def test_a_null_key_list_is_unknown_not_app_password(monkeypatch):
    """D1 (plan): NULL `configured_keys` is a pre-079 row, so UNKNOWN.

    Reading it as `app_password` would reveal a password field to an OAuth user
    whose row simply predates the column. This is the case that kills that
    coercion (ADR-219: "NULL means UNKNOWN").
    """
    src = (
        _client([_imap(A, "m")], monkeypatch, cred_rows=[_cred_row("imap", A, None)])
        .get("/sources")
        .json()["sources"][0]
    )

    assert src["auth_method"] is None


def test_auth_type_presence_does_not_classify(monkeypatch):
    """Both kinds of payload carry `auth_type`; it cannot discriminate."""
    src = (
        _client(
            [_imap(A, "m")],
            monkeypatch,
            cred_rows=[_cred_row("imap", A, ["auth_type", "host"])],
        )
        .get("/sources")
        .json()["sources"][0]
    )

    assert src["auth_method"] is None


def test_contradictory_keys_are_unknown(monkeypatch):
    """The generic credential PUT can write both; each wrong guess would mislead."""
    src = (
        _client(
            [_imap(A, "m")],
            monkeypatch,
            cred_rows=[_cred_row("imap", A, ["password", "refresh_token"])],
        )
        .get("/sources")
        .json()["sources"][0]
    )

    assert src["auth_method"] is None


def test_the_smtp_record_is_never_read_as_the_auth_method(monkeypatch):
    """Only the IMAP record classifies. An SMTP record for the same instance that
    happens to hold OAuth-looking keys must not leak into the answer."""
    cred_rows = [_cred_row("smtp", A, ["app_password", "reuse_imap", "refresh_token"])]

    src = (
        _client([_imap(A, "m")], monkeypatch, cred_rows=cred_rows)
        .get("/sources")
        .json()["sources"][0]
    )

    assert src["auth_method"] is None


def test_non_imap_rows_always_carry_a_null_auth_method(monkeypatch):
    """The IMAP gate, and key presence. An RSS row whose id collides with an IMAP
    record's instance must still be `null` (and the key must exist: `null` is the
    wire value, not an absent key)."""
    rows = [
        _imap(B, "mailbox"),
        _row(id=A, name="feed", source_type="rss", url="https://example.com/feed.xml"),
    ]
    cred_rows = [_cred_row("imap", A, OAUTH_KEYS), _cred_row("imap", B, OAUTH_KEYS)]

    srcs = _client(rows, monkeypatch, cred_rows=cred_rows).get("/sources").json()["sources"]

    by_name = {s["name"]: s for s in srcs}
    assert by_name["mailbox"]["auth_method"] == "oauth"
    assert "auth_method" in by_name["feed"]
    assert by_name["feed"]["auth_method"] is None


def test_listing_reads_credentials_once_and_never_decrypts(monkeypatch):
    """One metadata SELECT for 3 IMAP rows + 1 RSS row, and zero decrypts.

    `pytest.fail` raises `Failed`, a BaseException, so the route's
    `except Exception` degrade cannot swallow it. A guard raising RuntimeError
    would be swallowed, and the test would then pass only because of the
    positive-value assert below. The REAL `ccs.list_records` runs against `_Store`.
    """
    from services import connector_credentials as ccs
    from services import email_credentials

    def _boom(*a, **k):
        pytest.fail("list_sources must not decrypt")

    for name in ("get_payload", "resolve", "_decrypt_payload"):
        monkeypatch.setattr(ccs, name, _boom)
    monkeypatch.setattr(email_credentials, "load_connection", _boom)

    rows = [
        _imap(A, "one"),
        _imap(B, "two"),
        _imap(C, "three"),
        _row(id=D, name="feed", source_type="rss", url="https://example.com/feed.xml"),
    ]
    cred_rows = [
        _cred_row("imap", A, OAUTH_KEYS),
        _cred_row("imap", B, APP_PASSWORD_KEYS),
        _cred_row("imap", C, OAUTH_KEYS),
    ]
    client = _client(rows, monkeypatch, cred_rows=cred_rows)

    r = client.get("/sources")

    assert r.status_code == 200
    assert len(client.store.cred_calls) == 1
    assert client.store.cred_calls[0][1] == ("u1",)  # the caller's user_id, nobody else's
    # Without a positive value a degraded `{}` would also satisfy "no decrypt".
    assert any(s["auth_method"] == "oauth" for s in r.json()["sources"])


def test_a_credential_read_failure_degrades_to_unknown(monkeypatch, caplog):
    client = _client([_imap(A, "m")], monkeypatch, cred_error=RuntimeError("SELECT … secret-ish"))

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        r = client.get("/sources")

    assert r.status_code == 200
    srcs = r.json()["sources"]
    assert [s["auth_method"] for s in srcs] == [None]
    assert "smtp_host_required" in srcs[0]  # the rest of the row still renders
    msgs = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("credential metadata read failed" in m for m in msgs)
    assert not any("secret-ish" in m for m in msgs), "the type name only, never the message"


def test_records_are_matched_on_instance_not_connector_alone(monkeypatch):
    cred_rows = [_cred_row("imap", A, OAUTH_KEYS), _cred_row("imap", B, APP_PASSWORD_KEYS)]

    srcs = (
        _client([_imap(A, "x"), _imap(B, "y")], monkeypatch, cred_rows=cred_rows)
        .get("/sources")
        .json()["sources"]
    )

    assert {s["name"]: s["auth_method"] for s in srcs} == {"x": "oauth", "y": "app_password"}


def test_a_listing_without_mailboxes_never_reads_credentials(monkeypatch, caplog):
    rows = [
        _row(id=A, name="feed", source_type="rss", url="https://example.com/feed.xml"),
        _row(id=B, name="page", source_type="web", url="https://example.com/news"),
    ]
    client = _client(rows, monkeypatch, cred_error=RuntimeError("boom"))

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        r = client.get("/sources")

    assert r.status_code == 200
    assert client.store.cred_calls == []
    assert [s["auth_method"] for s in r.json()["sources"]] == [None, None]
    assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]
