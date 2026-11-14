# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""OAuth token refresh: negative cache honouring Retry-After + one never-sent retry (LUM-761).

Proven at the ``get_access_token`` seam through a real ``httpx.Client`` over a
``MockTransport`` and a fake clock — no sleeping, no network. The cache is reset
between tests by the autouse fixture in ``conftest.py``.

The fakes here fail the way the real collaborator fails: neither real httpx
transport path returns ``[]`` for a dead endpoint, and a tethered block is a
``RuntimeError`` (not an ``OSError``), so it travels raw through httpx.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import sys
import threading
import types
from datetime import datetime
from datetime import timezone
from email.utils import formatdate

import httpx
import pytest
from connectors.registry import IMAP
from services.email_oauth import OAuthRefreshError

from services import connector_credentials as ccs
from services import email_oauth as eo

_D = 1_700_000_000.0  # a fixed "server" instant for HTTP-date tests


class Rig:
    """Fake clock + scripted token endpoint + spies, installed on ``email_oauth``."""

    def __init__(self, monkeypatch, *, mock_http: bool = True):
        self.clock = [1000.0]
        self.wall = [_D]
        self.calls = 0
        self.pauses: list[int] = []
        self.client_kwargs: list[dict] = []
        self.puts: list[dict] = []
        self.client_id: str | None = "cid"
        self.payload_store = None  # what ccs.get_payload returns (None → caller's payload)
        self.script = lambda n, req: self.token("NEW")

        monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "false")
        monkeypatch.setattr(eo, "_client_id", lambda _p: self.client_id)
        monkeypatch.setattr(eo, "_now_monotonic", lambda: self.clock[0])
        monkeypatch.setattr(eo, "_now_wall", lambda: self.wall[0])
        monkeypatch.setattr(eo, "_retry_pause", lambda: self.pauses.append(1))
        monkeypatch.setattr(ccs, "get_payload", lambda *a, **k: self.payload_store)

        def fake_put(user_id, connector, payload, *, actor, instance=""):
            self.puts.append({"payload": payload, "instance": instance})

        monkeypatch.setattr(ccs, "put_payload", fake_put)

        if mock_http:
            real_client = httpx.Client

            def factory(*args, **kwargs):
                self.client_kwargs.append(dict(kwargs))
                kwargs["transport"] = httpx.MockTransport(self._handler)
                return real_client(*args, **kwargs)

            monkeypatch.setattr(httpx, "Client", factory)

    @staticmethod
    def token(value: str = "NEW", **extra) -> httpx.Response:
        return httpx.Response(200, json={"access_token": value, "expires_in": 3600, **extra})

    def _handler(self, request):
        self.calls += 1
        if self.calls > 5:
            # Lands in _post_refresh's non-httpx branch (network, uncached), so a
            # mutation that loops terminates and fails on its call count.
            raise AssertionError("call cap")
        out = self.script(self.calls, request)
        if isinstance(out, BaseException):
            raise out
        return out

    def advance(self, seconds: float) -> None:
        self.clock[0] += seconds

    def call(self, *, user="u1", instance="src-1", rt="rt-1", **payload_extra):
        payload = {"provider": "microsoft", "refresh_token": rt, **payload_extra}
        return eo.get_access_token(user, connector=IMAP, instance=instance, payload=payload)

    def fails(self, **kw) -> OAuthRefreshError:
        with pytest.raises(OAuthRefreshError) as ei:
            self.call(**kw)
        return ei.value


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


def _always(resp_factory):
    return lambda n, req: resp_factory()


# --------------------------------------------------------------------------- #
# Retry-After is honoured by NOT CALLING                                      #
# --------------------------------------------------------------------------- #


def test_a_429_with_retry_after_is_not_called_again_until_it_elapses(rig):
    rig.script = _always(lambda: httpx.Response(429, headers={"Retry-After": "120"}))

    assert rig.fails().kind == "network"
    assert rig.calls == 1

    rig.advance(119)
    exc = rig.fails()
    assert exc.kind == "network"
    assert rig.calls == 1
    assert "last failure: 429" in str(exc)
    assert "retry in ~1 s" in str(exc)
    assert "deferred" not in str(exc)

    rig.advance(2)
    rig.fails()
    assert rig.calls == 2


@pytest.mark.parametrize("status", [503, 429])
def test_a_5xx_without_retry_after_backs_off_for_the_default_ttl(rig, status):
    rig.script = _always(lambda: httpx.Response(status))
    rig.fails()
    rig.advance(9)
    rig.fails()
    assert rig.calls == 1
    rig.advance(2)
    rig.fails()
    assert rig.calls == 2


@pytest.mark.parametrize(
    ("header", "still_deferred_at", "posts_at"),
    [("99999", 599, 601), ("0", 4, 6)],
)
def test_retry_after_is_clamped_to_the_cap_and_the_floor(rig, header, still_deferred_at, posts_at):
    rig.script = _always(lambda: httpx.Response(503, headers={"Retry-After": header}))
    rig.fails()
    rig.advance(still_deferred_at)
    rig.fails()
    assert rig.calls == 1
    rig.advance(posts_at - still_deferred_at)
    rig.fails()
    assert rig.calls == 2


_HTTP_DATE_TS = datetime(2015, 10, 21, 7, 28, 0, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        ("120", 0.0, 120.0),
        (" 7 ", 0.0, 7.0),
        ("0000000000005", 0.0, 5.0),
        ("Wed, 21 Oct 2015 07:28:00 GMT", _HTTP_DATE_TS - 30, 30.0),
        ("Wed, 21 Oct 2015 07:28:00 GMT", _HTTP_DATE_TS + 60, 0.0),
        # asctime: the stdlib returns a NAIVE datetime; RFC 9110 §5.6.7 says accept it.
        ("Wed Oct 21 07:28:00 2015", _HTTP_DATE_TS - 30, 30.0),
        ("Wednesday, 21-Oct-15 07:28:00 GMT", _HTTP_DATE_TS - 30, 30.0),
        ("9" * 5000, 0.0, 1e9),
        (None, 0.0, None),
        ("", 0.0, None),
        ("soon", 0.0, None),
        ("-5", 0.0, None),
        ("1.5", 0.0, None),
        ("١٢", 0.0, None),
    ],
)
def test_parse_retry_after_accepts_seconds_and_http_date_and_rejects_garbage(
    value, reference, expected
):
    assert eo._parse_retry_after(value, reference_wall=reference) == expected


def test_an_oversized_delay_clamps_to_the_cap_and_http_date_parsing_never_raises():
    assert eo._backoff_ttl(1e9) == 600.0
    for bad in ("", "soon", "Wed, 21 Oct 99999999 07:28:00 GMT"):
        assert eo._http_date_ts(bad) is None


# --------------------------------------------------------------------------- #
# What is NOT cached or retried                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "response",
    [
        lambda: httpx.Response(400, json={"error": "invalid_grant"}),
        lambda: httpx.Response(401, json={}),
        lambda: httpx.Response(403, json={}),
        lambda: httpx.Response(408, json={}),
    ],
)
def test_credential_failures_are_neither_retried_nor_cached(rig, response):
    rig.script = lambda n, req: response() if n == 1 else rig.token()
    assert rig.fails().kind == "credential"
    assert rig.calls == 1
    assert rig.pauses == []
    assert rig.call() == "NEW"
    assert rig.calls == 2


def test_config_failures_are_not_cached_and_win_over_a_live_entry(rig):
    rig.script = lambda n, req: (
        httpx.Response(400, json={"error": "invalid_client"}) if n == 1 else rig.token()
    )
    assert rig.fails().kind == "config"
    assert rig.call() == "NEW"
    assert rig.calls == 2

    eo._reset_refresh_backoff()
    rig.client_id = None
    assert rig.fails().kind == "config"
    assert eo._REFRESH_BACKOFF == {}

    # A live entry must not turn a removed client_id into a `network` message.
    calls = rig.calls
    eo._record_backoff(
        eo._backoff_key("u1", IMAP, "src-1", "rt-1"),
        60,
        provider="microsoft",
        last_failure="503",
        retry_after_present=False,
    )
    assert rig.fails().kind == "config"
    assert rig.calls == calls


# --------------------------------------------------------------------------- #
# The never-sent retry                                                        #
# --------------------------------------------------------------------------- #


def test_a_fast_connect_error_is_retried_once_and_can_succeed(rig):
    rig.script = lambda n, req: httpx.ConnectError("refused") if n == 1 else rig.token()
    assert rig.call() == "NEW"
    assert rig.calls == 2
    assert rig.pauses == [1]
    assert len(rig.puts) == 1
    assert eo._REFRESH_BACKOFF == {}


def test_a_fast_connect_error_is_retried_at_most_once_and_held_briefly(rig):
    rig.script = _always(lambda: httpx.ConnectError("refused"))
    assert rig.fails().kind == "network"
    assert rig.calls == 2
    assert rig.pauses == [1]
    (entry,) = eo._REFRESH_BACKOFF.values()
    assert entry.last_failure == "ConnectError"

    rig.advance(9)
    exc = rig.fails()
    assert rig.calls == 2
    assert "last failure: ConnectError" in str(exc)

    rig.advance(2)
    rig.fails()
    assert rig.calls == 4  # POSTed again, with its own single retry


def _fake_tethered():
    fake = types.ModuleType("tethered")

    class EgressBlocked(RuntimeError):  # RuntimeError base, as in tethered 0.5.1
        def __init__(self, host="login.microsoftonline.com", port=443):
            super().__init__("blocked")
            self.host, self.port = host, port

    fake.EgressBlocked = EgressBlocked
    fake.scope = lambda **kw: contextlib.nullcontext()
    return fake


@pytest.mark.parametrize("shape", ["raw", "wrapped"])
def test_an_egress_block_is_never_retried_or_cached(rig, monkeypatch, shape):
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    fake = _fake_tethered()
    monkeypatch.setitem(sys.modules, "tethered", fake)

    def script(n, req):
        if shape == "raw":
            return fake.EgressBlocked()
        err = httpx.ConnectError("blocked")
        err.__cause__ = fake.EgressBlocked()
        return err

    rig.script = script
    assert rig.fails().kind == "egress_blocked"
    assert rig.calls == 1
    assert rig.pauses == []
    assert eo._REFRESH_BACKOFF == {}


def test_a_real_tethered_block_is_egress_blocked_and_not_retried(monkeypatch):
    pytest.importorskip("tethered")
    rig = Rig(monkeypatch, mock_http=False)  # real httpx.Client, real tethered
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    # TEST-NET-3, undeclared; the scope allows only the real token host.
    monkeypatch.setitem(eo._PROVIDER_TOKEN, "microsoft", "https://203.0.113.1/token")
    monkeypatch.setattr(eo, "_token_host", lambda _p: "login.microsoftonline.com")

    assert rig.fails().kind == "egress_blocked"
    assert rig.pauses == []
    assert eo._REFRESH_BACKOFF == {}


def test_a_slow_connect_error_is_not_retried(rig, monkeypatch):
    monkeypatch.setattr(eo, "_REFRESH_DEADLINE_S", 1000.0)  # only `fast` may gate the retry

    def script(n, req):
        rig.advance(4)
        return httpx.ConnectError("blackholed")

    rig.script = script
    assert rig.fails().kind == "network"
    assert rig.calls == 1
    assert rig.pauses == []
    (entry,) = eo._REFRESH_BACKOFF.values()
    assert entry.not_before - rig.clock[0] == 10.0


@pytest.mark.parametrize(
    "outcome",
    [
        lambda: httpx.ReadTimeout("t"),
        lambda: httpx.ConnectTimeout("t"),
        lambda: httpx.RemoteProtocolError("p"),
        lambda: httpx.WriteTimeout("t"),
        lambda: httpx.ProxyError("denied"),
        lambda: httpx.Response(200, text="<html>login</html>"),
        lambda: httpx.Response(200, json=[]),
    ],
    ids=[
        "ReadTimeout",
        "ConnectTimeout",
        "RemoteProtocolError",
        "WriteTimeout",
        "ProxyError",
        "html-200",
        "json-list-200",
    ],
)
def test_ambiguous_failures_are_not_retried_in_place(rig, outcome):
    rig.script = _always(outcome)
    assert rig.fails().kind == "network"
    assert rig.calls == 1
    assert rig.pauses == []
    rig.advance(9)
    rig.fails()
    assert rig.calls == 1
    rig.advance(2)
    rig.fails()
    assert rig.calls == 2


def test_the_timeouts_are_split_and_wired(rig):
    assert (
        eo._FAST_CONNECT_FAILURE_S + eo._RETRY_PAUSE_RANGE_S[1] + eo._REFRESH_ATTEMPT_WORST_CASE_S
        <= eo._REFRESH_DEADLINE_S
    )
    assert rig.call() == "NEW"
    assert rig.client_kwargs[0]["timeout"] == httpx.Timeout(
        connect=5.0, read=15.0, write=5.0, pool=5.0
    )


def test_the_retry_is_skipped_when_the_deadline_has_no_room(rig, monkeypatch):
    monkeypatch.setattr(eo, "_REFRESH_DEADLINE_S", 10.0)
    rig.script = _always(lambda: httpx.ConnectError("refused"))
    assert rig.fails().kind == "network"
    assert rig.calls == 1
    assert rig.pauses == []  # a refused retry does not pause
    assert len(eo._REFRESH_BACKOFF) == 1


# --------------------------------------------------------------------------- #
# Waiters                                                                     #
# --------------------------------------------------------------------------- #


def test_a_waiter_whose_holder_just_failed_does_not_post(rig, monkeypatch):
    payload = {"provider": "microsoft", "refresh_token": "rt-1"}

    def holder_failed_meanwhile(*a, **k):
        eo._record_backoff(
            eo._backoff_key("u1", IMAP, "src-1", "rt-1"),
            60,
            provider="microsoft",
            last_failure="503",
            retry_after_present=False,
        )
        return payload

    monkeypatch.setattr(ccs, "get_payload", holder_failed_meanwhile)
    assert rig.fails().kind == "network"
    assert rig.calls == 0


def test_concurrent_callers_during_an_outage_make_one_post(rig):
    entered, release = threading.Event(), threading.Event()

    def script(n, req):
        entered.set()
        release.wait(5)
        return httpx.Response(503)

    rig.script = script
    errors: list[BaseException] = []

    def run():
        try:
            rig.call()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    a = threading.Thread(target=run)
    a.start()
    assert entered.wait(5)
    b = threading.Thread(target=run)
    b.start()
    release.set()
    a.join(10)
    b.join(10)

    assert len(errors) == 2
    assert all(isinstance(e, OAuthRefreshError) and e.kind == "network" for e in errors)
    assert rig.calls == 1


# --------------------------------------------------------------------------- #
# Key shape, isolation, clearing, pruning                                     #
# --------------------------------------------------------------------------- #


def test_a_reconnect_bypasses_the_old_entry(rig):
    rig.script = _always(lambda: httpx.Response(503))
    rig.fails(rt="rt-1")
    assert len(eo._REFRESH_BACKOFF) == 1

    rig.script = _always(lambda: rig.token())
    assert rig.call(rt="rt-2") == "NEW"
    assert eo._REFRESH_BACKOFF == {}  # cleared by mailbox prefix, so rt-1's entry is gone


def test_one_entry_per_mailbox_and_isolation_between_mailboxes_and_users(rig):
    rig.script = _always(lambda: httpx.Response(503))
    rig.fails(rt="rt-1")
    rig.fails(rt="rt-2")
    assert rig.calls == 2
    assert [k[:3] for k in eo._REFRESH_BACKOFF] == [("u1", IMAP, "src-1")]

    rig.fails(instance="src-2")
    rig.fails(user="u2")
    assert rig.calls == 4  # neither was deferred by u1/src-1's entry


def test_a_valid_cached_access_token_is_never_blocked_by_the_cache(rig):
    eo._record_backoff(
        eo._backoff_key("u1", IMAP, "src-1", "rt-1"),
        60,
        provider="microsoft",
        last_failure="503",
        retry_after_present=False,
    )
    tok = rig.call(access_token="CACHED", expiry=eo.time.time() + 3600)
    assert tok == "CACHED"
    assert rig.calls == 0


def test_expired_entries_are_pruned_on_read_and_write(rig):
    def rec(instance):
        key = eo._backoff_key("u1", IMAP, instance, "rt-1")
        eo._record_backoff(
            key, 10, provider="microsoft", last_failure="503", retry_after_present=False
        )
        return key

    a = rec("src-1")
    rig.advance(11)
    b = rec("src-2")
    assert a not in eo._REFRESH_BACKOFF  # pruned on write

    a = rec("src-1")
    rig.advance(11)
    assert eo._backoff_entry(b) is None
    assert a not in eo._REFRESH_BACKOFF  # pruned on read


# --------------------------------------------------------------------------- #
# Hygiene, clocks, unexpected errors                                          #
# --------------------------------------------------------------------------- #


def test_the_backoff_never_logs_or_raises_token_material(rig, caplog):
    caplog.set_level(logging.DEBUG, logger="services.email_oauth")
    raised: list[OAuthRefreshError] = []

    rig.script = _always(lambda: httpx.Response(429, headers={"Retry-After": "120"}))
    raised.append(rig.fails())
    rig.advance(5)
    raised.append(rig.fails())  # a cache hit

    eo._reset_refresh_backoff()
    # The transport error's own text echoes the token; OUR message and logs must not.
    rig.script = _always(lambda: httpx.ConnectError("refused: rt-1"))
    raised.append(rig.fails())
    rig.advance(5)
    raised.append(rig.fails())

    # Positive controls: without them a displaced caplog makes the absence checks vacuous.
    assert "oauth_refresh_backoff provider=" in caplog.text
    assert "oauth_refresh_backoff_hit provider=" in caplog.text

    digest = hashlib.sha256(b"rt-1").hexdigest()[:16]
    for needle in ("rt-1", digest):
        assert needle not in caplog.text
        for exc in raised:
            assert needle not in str(exc)
            assert needle not in repr(exc.args)


def test_the_real_retry_pause_is_bounded_and_jittered(monkeypatch, real_oauth_retry_pause):
    import random

    slept: list[float] = []
    seen: list[tuple[float, float]] = []
    monkeypatch.setattr(eo.time, "sleep", slept.append)
    for bound in eo._RETRY_PAUSE_RANGE_S:
        monkeypatch.setattr(random, "uniform", lambda a, b, _v=bound: seen.append((a, b)) or _v)
        eo._retry_pause()
    assert slept == [0.5, 1.0]
    assert seen == [eo._RETRY_PAUSE_RANGE_S] * 2


def test_a_cached_hit_reaches_the_send_path_as_smtp_send_unavailable(rig, monkeypatch):
    from actions.errors import PermanentActionError
    from actions.handlers import smtp_send
    from signals import feed_monitor

    from services import email_credentials

    xoauth2 = {
        "auth_type": "xoauth2",
        "provider": "microsoft",
        "refresh_token": "rt-1",
        "host": "outlook.office365.com",
        "port": 993,
        "username": "a@example.com",
    }
    monkeypatch.setattr(
        ccs, "get_payload", lambda uid, connector, instance="": xoauth2 if connector == IMAP else {}
    )
    eo._record_backoff(
        eo._backoff_key("u1", IMAP, "src-1", "rt-1"),
        60,
        provider="microsoft",
        last_failure="503",
        retry_after_present=False,
    )

    with pytest.raises(OAuthRefreshError) as ei:
        email_credentials.load_connection("u1", instance="src-1")
    assert ei.value.kind == "network"

    with pytest.raises(PermanentActionError) as pi:
        smtp_send._resolve_smtp("src-1", "u1")
    assert pi.value.code == "smtp_send_unavailable"
    assert isinstance(pi.value.__cause__, OAuthRefreshError)
    assert pi.value.__cause__.kind == "network"

    assert feed_monitor._classify_construction_failure(ei.value) == "network"
    assert rig.calls == 0


def test_an_http_date_retry_after_uses_the_response_date_then_the_wall_clock(rig):
    # (a) a skewed local clock: the response's own Date is the reference.
    rig.wall[0] = _D + 3600
    rig.script = _always(
        lambda: httpx.Response(
            503,
            headers={
                "Date": formatdate(_D, usegmt=True),
                "Retry-After": formatdate(_D + 90, usegmt=True),
            },
        )
    )
    rig.fails()
    rig.advance(89)
    rig.fails()
    assert rig.calls == 1
    rig.advance(2)
    rig.fails()
    assert rig.calls == 2

    # (b) no Date header: the wall clock is the reference.
    eo._reset_refresh_backoff()
    rig.calls = 0
    rig.wall[0] = _D
    rig.script = _always(
        lambda: httpx.Response(
            503, headers={"Retry-After": formatdate(rig.wall[0] + 90, usegmt=True)}
        )
    )
    rig.fails()
    rig.advance(89)
    rig.fails()
    assert rig.calls == 1
    rig.advance(2)
    rig.fails()
    assert rig.calls == 2


def test_an_unexpected_exception_is_network_but_not_cached(rig, caplog):
    caplog.set_level(logging.DEBUG, logger="services.email_oauth")
    rig.script = lambda n, req: KeyError("boom") if n == 1 else rig.token()

    assert rig.fails().kind == "network"
    assert "oauth_refresh_unexpected_error" in caplog.text
    assert "exc_type=KeyError" in caplog.text
    assert "boom" not in caplog.text
    assert "rt-1" not in caplog.text
    assert eo._REFRESH_BACKOFF == {}
    assert rig.call() == "NEW"
    assert rig.calls == 2
