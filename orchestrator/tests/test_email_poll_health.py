# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-source poll health (LUM-690 / LUM-633) — a mailbox that can't currently
authenticate (revoked/expired OAuth grant, a soft-saved OAuth connect on an
egress-proxied deployment whose first real sync then fails, or a broken
app-password credential) is recorded on ``sources`` instead of only logging
``email_poll_skip code=credential`` forever with the mailbox still showing as
connected. Covers the shared ``_record_poll_health`` helper directly and both
call sites (the metadata poll and the opt-in full-ingest poll).
"""

from __future__ import annotations

from models.signals import SourceConfig
from signals import feed_monitor as fm

import config as _config


def _source(**kw) -> SourceConfig:
    base = dict(
        id="src-1",
        name="mail",
        source_type="imap",
        url="imap://h/1/1",
        category="",
        active=True,
        poll_interval=900,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="u1",
    )
    base.update(kw)
    return SourceConfig(**base)


class _HealthStore:
    """Captures every ``UPDATE sources SET last_poll_error_*`` write."""

    def __init__(self):
        self.updates: list[tuple] = []

    def execute(self, query, params=None):
        q = " ".join((query or "").split()).lower()
        if "update sources set last_poll_error_code" in q:
            self.updates.append((q, params))
            return
        # Anything else (poll_cursor writes, etc.) is a no-op in this store —
        # these tests only assert on the health-write behaviour.
        return

    def fetch_one(self, query, params=None):
        return None

    def fetch_all(self, query, params=None):
        return []


class _RaisingAdapter:
    """Construction always raises — simulates OAuthRefreshError / a broken
    app-password credential (the exact site ``email_credentials.load_connection``
    raises from inside ``EmailImapSource.__init__``)."""

    def __init__(self, source, *, client_factory=None):
        raise RuntimeError("credential unavailable")


class _OkAdapter:
    """Construction succeeds; ``poll``/``inbox_seed`` return nothing interesting."""

    def __init__(self, source, *, client_factory=None):
        self.result_cursor = None

    def inbox_seed(self):
        return (100, 0)

    def poll(self, *, since=None, reseed_on_uidvalidity_change=False):
        return []


def _store(monkeypatch) -> _HealthStore:
    store = _HealthStore()
    _config._instances["metadata_store"] = store
    monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: "100:0")
    monkeypatch.setattr(fm, "_fetch_import_row", lambda sid: {})
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda s: None)
    return store


# --- _record_poll_health (unit) --------------------------------------------- #


def test_record_poll_health_failure_writes_code_and_timestamp(monkeypatch):
    store = _store(monkeypatch)
    fm._record_poll_health("src-1", ok=False, error_code="credential")
    assert len(store.updates) == 1
    q, params = store.updates[0]
    assert "null" not in q  # the failure branch sets values, not NULL
    assert params[0] == "credential" and params[2] == "src-1"


def test_record_poll_health_ok_writes_null(monkeypatch):
    store = _store(monkeypatch)
    fm._record_poll_health("src-1", ok=True)
    assert len(store.updates) == 1
    q, params = store.updates[0]
    assert "= null" in q
    assert params == ("src-1",)


def test_record_poll_health_write_failure_does_not_raise(monkeypatch):
    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    _config._instances["metadata_store"] = _Boom()
    fm._record_poll_health("src-1", ok=False, error_code="credential")  # must not raise


# --- _poll_email_source (metadata path) ------------------------------------- #


def test_metadata_poll_construction_failure_marks_unhealthy(monkeypatch):
    store = _store(monkeypatch)
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _RaisingAdapter)
    fm._poll_email_source(_source())
    assert len(store.updates) == 1
    _, params = store.updates[0]
    assert params[0] == "credential"


def test_metadata_poll_success_clears_health(monkeypatch):
    store = _store(monkeypatch)
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _OkAdapter)
    monkeypatch.setattr(fm, "_persist_poll_cursor", lambda sid, cur: None)
    monkeypatch.setattr(fm, "_is_duplicate", lambda url, user_id: False)
    monkeypatch.setattr(fm, "process_signal", lambda raw, user_id=None, **k: None)
    monkeypatch.setattr(fm, "_run_organize_tick", lambda source: None)
    monkeypatch.setattr(fm, "_run_cleanup_tick", lambda source: None)
    fm._poll_email_source(_source(poll_cursor="100:0"))
    assert len(store.updates) == 1
    q, params = store.updates[0]
    assert "= null" in q and params == ("src-1",)


# --- _poll_email_ingest_source (opt-in full-ingest path) -------------------- #


def test_full_ingest_construction_failure_marks_unhealthy(monkeypatch):
    store = _store(monkeypatch)
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _RaisingAdapter)
    fm._poll_email_ingest_source(_source(extraction_method="imap_full_ingest"))
    assert len(store.updates) == 1
    _, params = store.updates[0]
    assert params[0] == "credential"


def test_full_ingest_mid_poll_failure_does_not_mark_credential_unhealthy(monkeypatch):
    # A live-poll failure AFTER construction succeeded (e.g. a transient IMAP
    # error while fetching) is NOT the same failure class as "can't
    # authenticate" — the construction-succeeded ok=True write fires, but no
    # ok=False write follows it (that would misreport a transient fetch hiccup
    # as "reconnect your mailbox").
    store = _store(monkeypatch)

    class _PollRaisingAdapter(_OkAdapter):
        def poll(self, *, since=None, reseed_on_uidvalidity_change=False):
            raise RuntimeError("transient fetch error")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _PollRaisingAdapter)
    fm._poll_email_ingest_source(_source(extraction_method="imap_full_ingest"))
    assert len(store.updates) == 1  # only the ok=True clear from construction
    q, _params = store.updates[0]
    assert "= null" in q


# --------------------------------------------------------------------------- #
# LUM-633 — health must survive the CONNECT, not stop at construction.
#
# Construction opens no IMAP socket, so a mailbox blocked by egress containment
# used to construct fine, get stamped HEALTHY, and then fail at connect where
# nothing stamped it. The surface did not merely omit the failure — it
# affirmatively reported the mailbox as fine.
#
# Assertions are in WRITE-LOG terms because `_HealthStore` is a write log, not
# a store: it records UPDATEs and `fetch_one` returns None, so there is no
# state to pre-seed and read back.
# --------------------------------------------------------------------------- #


def _codes(store) -> list[str | None]:
    """The error code of each health write, in order (None = the clear)."""
    out = []
    for q, params in store.updates:
        out.append(None if "= null" in q else params[0])
    return out


class _ConnectFailingAdapter:
    """Constructs fine (as the real adapter does) and fails when it connects."""

    exc: BaseException = RuntimeError("boom")

    def __init__(self, source, *, client_factory=None):
        self.result_cursor = None

    def inbox_seed(self):
        raise type(self).exc

    def poll(self, *, since=None, reseed_on_uidvalidity_change=False):
        raise type(self).exc


def _adapter_raising(monkeypatch, exc: BaseException):
    cls = type("_Raiser", (_ConnectFailingAdapter,), {"exc": exc})
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", cls)
    return cls


def test_an_egress_blocked_poll_is_not_recorded_as_healthy(monkeypatch):
    """THE regression test. On pre-LUM-633 code this fails: the source is
    stamped healthy right after construction, before the connect that blocks."""
    from services.egress_guard import EgressBlockedError

    store = _store(monkeypatch)
    _adapter_raising(monkeypatch, EgressBlockedError(host="mail.example.invalid", port=993))
    fm._poll_email_source(_source())
    assert _codes(store) == ["egress_blocked"]


def test_a_connect_auth_failure_is_recorded_as_credential(monkeypatch):
    from adapters.email_imap_source import ImapConnectError

    store = _store(monkeypatch)
    _adapter_raising(monkeypatch, ImapConnectError("auth", "login failed"))
    fm._poll_email_source(_source())
    assert _codes(store) == ["credential"]


def test_an_unresolvable_host_is_network_at_the_connect_site(monkeypatch):
    """Paired with the construction-site test below: the SAME condition must
    produce the SAME code at both sites, or one mailbox gets two stories."""
    from services.email_credentials import ImapHostUnresolvedError

    store = _store(monkeypatch)
    _adapter_raising(monkeypatch, ImapHostUnresolvedError("did not resolve"))
    fm._poll_email_source(_source())
    assert _codes(store) == ["network"]


def test_an_unresolvable_host_is_network_at_the_construction_site(monkeypatch):
    """The other half of the pair. Before LUM-633 this was a bare ValueError and
    would have been stamped `config` — "will not fix itself" — for a textbook
    transient DNS failure."""
    from services.email_credentials import ImapHostUnresolvedError

    store = _store(monkeypatch)

    class _Raiser:
        def __init__(self, source, *, client_factory=None):
            raise ImapHostUnresolvedError("did not resolve")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Raiser)
    fm._poll_email_source(_source())
    assert _codes(store) == ["network"]


def test_a_post_connect_failure_clears_health_rather_than_stamping_it(monkeypatch):
    """`poll()` raises a bare ValueError AFTER connecting — `int()` over a
    malformed server UIDNEXT. Reaching that proves the mailbox connected, so a
    stale error must clear. Stamping it `network` would make the mailbox flap
    on one bad message, which is what teaches people to ignore the banner."""
    store = _store(monkeypatch)
    _adapter_raising(monkeypatch, ValueError("invalid literal for int()"))
    fm._poll_email_source(_source())
    assert _codes(store) == [None]


def test_the_full_ingest_path_is_fixed_too(monkeypatch):
    """The twin defect. `EmailSignalsView` has a "Switch to full ingest" button,
    so leaving this path unfixed put the bug one click away from the banner
    meant to warn about it."""
    from services.egress_guard import EgressBlockedError

    store = _store(monkeypatch)
    monkeypatch.setattr(fm, "_email_full_ingest_enabled", lambda: True)
    _adapter_raising(monkeypatch, EgressBlockedError(host="mail.example.invalid", port=993))
    fm._poll_email_ingest_source(_source(extraction_method="imap_full_ingest"))
    assert "egress_blocked" in _codes(store)


# --- LUM-759: one classifier for one raise surface --------------------------- #


def test_the_shared_classifier_is_the_one_poll_health_uses():
    """ADR 195's "the classifiers must agree" rule, made mechanical.

    LUM-759 moved the mapping to `email_credentials.classify_credential_failure`
    so the SMTP opt-in route classifies the same raise surface the same way.

    **Known limit, so nobody over-reads the name:** this compares two functions'
    outputs. It passes if someone reimplements the mapping identically, and fails
    only once the two DRIFT — which is the property ADR 195 actually states, not
    proof of delegation.
    """
    from services.email_oauth import OAuthRefreshError
    from signals import feed_monitor as fm

    from services import email_credentials as ec

    cases: list[BaseException] = [
        OAuthRefreshError("x", kind="credential"),
        OAuthRefreshError("x", kind="network"),
        OAuthRefreshError("x", kind="config"),
        OAuthRefreshError("x", kind="egress_blocked"),
        ec.ImapHostBlockedError("policy"),
        ec.ImapHostUnresolvedError("dns"),
        ValueError("bad port"),
        RuntimeError("who knows"),
    ]
    for exc in cases:
        assert fm._classify_construction_failure(exc) == ec.classify_credential_failure(exc), (
            f"poll health and the SMTP route disagree about {type(exc).__name__}"
        )


# --- non-IMAP sources (LUM-837) --------------------------------------------- #
#
# Until LUM-837 only the IMAP paths recorded health, so a paperless source with
# no credential — or an RSS feed whose host had gone away — advanced
# `last_polled_at` on every tick and recorded nothing. Any consumer of those
# columns read a permanently dead source as freshly polled, which is exactly
# what the sources list would have done.


def _paperless_source(**kw) -> SourceConfig:
    return _source(
        source_type="paperless",
        url="https://paperless.example.com",
        extraction_method="paperless_http",
        **kw,
    )


def test_paperless_poll_without_a_credential_marks_unhealthy(monkeypatch):
    store = _store(monkeypatch)
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda source: None)
    monkeypatch.setattr(fm, "_fetch_poll_cursor", lambda sid: None)

    from services import paperless_credentials as pwc

    def _no_credential(user_id, **kw):
        raise RuntimeError("no paperless credential row")

    monkeypatch.setattr(pwc, "load_connection", _no_credential)

    fm._poll_paperless_source(_paperless_source())

    assert len(store.updates) == 1
    _, params = store.updates[0]
    assert params[0] == "credential"


def test_signal_poll_raising_marks_unhealthy(monkeypatch):
    """An adapter that raises — the rare case."""
    store = _store(monkeypatch)
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda source: None)

    class _RaisingAdapter:
        last_fetch_ok = True

        def poll(self):
            raise RuntimeError("host went away")

    monkeypatch.setattr(fm, "_build_adapter", lambda source: _RaisingAdapter())

    fm._poll_signal_source(_source(source_type="rss", url="https://feed.example.com/rss"))

    assert len(store.updates) == 1
    _, params = store.updates[0]
    assert params[0] == "poll_failed"


def test_signal_poll_failing_WITHOUT_raising_marks_unhealthy(monkeypatch):
    """The case that actually happens, and that the first version of this got
    wrong.

    **Neither real adapter raises on a fetch failure.**
    ``PageScraper._fetch_html`` catches and returns ``None``; ``feedparser.parse``
    does not raise on a network error at all. Both just return ``[]`` — which is
    indistinguishable from "reached it, nothing new" unless the adapter is asked.

    The first version of this test used a fake that raised, so it proved a branch
    that essentially never executes in production while a dead feed was being
    recorded as healthy on every tick.
    """
    store = _store(monkeypatch)
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda source: None)

    class _QuietlyFailingAdapter:
        last_fetch_ok = False

        def poll(self):
            return []  # exactly what the real adapters do when the host is down

    monkeypatch.setattr(fm, "_build_adapter", lambda source: _QuietlyFailingAdapter())

    fm._poll_signal_source(_source(source_type="rss", url="https://feed.example.com/rss"))

    assert len(store.updates) == 1
    _, params = store.updates[0]
    assert params[0] == "poll_failed"


def test_signal_poll_reaching_a_quiet_feed_stays_healthy(monkeypatch):
    """The mirror image, and the reason the flag is needed rather than inferring
    from an empty list: a feed that answered with nothing new is HEALTHY, and
    returns exactly the same empty list as the dead one above."""
    store = _store(monkeypatch)
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda source: None)

    class _QuietButReachableAdapter:
        last_fetch_ok = True

        def poll(self):
            return []

    monkeypatch.setattr(fm, "_build_adapter", lambda source: _QuietButReachableAdapter())

    fm._poll_signal_source(_source(source_type="rss", url="https://feed.example.com/rss"))

    assert len(store.updates) == 1
    q, params = store.updates[0]
    assert "= null" in q and params == ("src-1",)


def test_signal_poll_success_clears_health(monkeypatch):
    store = _store(monkeypatch)
    monkeypatch.setattr(fm, "_update_poll_timestamp", lambda source: None)

    class _OkSignalAdapter:
        last_fetch_ok = True

        def poll(self):
            return []

    monkeypatch.setattr(fm, "_build_adapter", lambda source: _OkSignalAdapter())

    fm._poll_signal_source(_source(source_type="rss", url="https://feed.example.com/rss"))

    assert len(store.updates) == 1
    q, params = store.updates[0]
    assert "= null" in q and params == ("src-1",)
