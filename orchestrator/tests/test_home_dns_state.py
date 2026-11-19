# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""services.home_dns.state — persistence and the state machine (LUM-331)."""

from __future__ import annotations

import stat
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from services.home_dns import state as st

HID = "kq7f3r2abcdefghijklmnopqrs"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path))
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ZONE", raising=False)
    return tmp_path


def _ready(**kw) -> st.HomeDnsState:
    base = dict(
        enabled=True,
        household_id=HID,
        bearer="b" * 43,
        lan_address="192.168.1.20",
        registered_at=NOW - timedelta(days=1),
        snippet_applied_at=NOW - timedelta(days=1),
        cert_not_before=NOW - timedelta(days=10),
        cert_not_after=NOW + timedelta(days=80),
        resolver="ok",
    )
    base.update(kw)
    return st.HomeDnsState(**base)


def _derive(s, **kw):
    return st.derive(s, NOW, sign_in_enabled=True, **kw)


@pytest.mark.parametrize(
    "state,expected,code",
    [
        (st.HomeDnsState(last_error="state_corrupt"), "error", "state_corrupt"),
        (st.HomeDnsState(), "off", None),
        (_ready(enabled=False), "off", None),
        (_ready(last_error="caddy_volume_missing"), "error", "caddy_volume_missing"),
        (_ready(last_error="clock_skew"), "error", "clock_skew"),
        (_ready(lan_address=None), "needs_address", None),
        (_ready(registered_at=None), "registering", None),
        (
            _ready(registered_at=None, last_error="worker_unreachable"),
            "registering",
            "worker_unreachable",
        ),
        (
            _ready(cert_not_after=None, snippet_applied_at=NOW - timedelta(minutes=16)),
            "error",
            "certificate_pending",
        ),
        (
            _ready(cert_not_after=None, snippet_applied_at=NOW - timedelta(minutes=14)),
            "issuing",
            None,
        ),
        (
            _ready(cert_not_after=None, snippet_applied_at=NOW, last_error="budget_exhausted"),
            "issuing",
            "budget_exhausted",
        ),
        (
            _ready(cert_not_after=NOW + timedelta(days=1)),
            "error",
            "renewal_failing",
        ),  # 1 d < 11 d / 6
        (_ready(cert_not_after=NOW - timedelta(seconds=1)), "error", "renewal_failing"),  # lapsed
        (_ready(resolver="blocked", resolver_checked_at=NOW), "router_blocked", None),
        # a blocked verdict taken inside the "name is new" window is not a verdict
        (
            _ready(
                resolver="blocked",
                resolver_checked_at=NOW - timedelta(minutes=1),
                registered_at=NOW - timedelta(minutes=10),
                name_published_at=NOW - timedelta(minutes=10),
            ),
            "ready",
            None,
        ),
        (_ready(), "ready", None),
        # row 9: a non-blocking error stays "ready" and is shown as a warning
        (_ready(last_error="worker_unreachable"), "ready", "worker_unreachable"),
        (
            _ready(resolver="blocked", resolver_checked_at=NOW, last_error="caddy_reload_failed"),
            "router_blocked",
            "caddy_reload_failed",
        ),
        (_ready(last_error="lan_address_unknown"), "error", "lan_address_unknown"),
    ],
)
def test_derive_table(state, expected, code) -> None:
    view = _derive(state)
    assert view.state == expected
    assert view.error_code == code


def test_renewal_threshold_is_relative_to_lifetime() -> None:
    # A 6-day certificate with 2 days left is healthy (2 d > 1 d), not "failing".
    six_day = _ready(
        cert_not_before=NOW - timedelta(days=4), cert_not_after=NOW + timedelta(days=2)
    )
    assert _derive(six_day).state == "ready"
    nearly = _ready(
        cert_not_before=NOW - timedelta(days=5, hours=1), cert_not_after=NOW + timedelta(hours=23)
    )
    assert _derive(nearly).state == "error"


def test_url_and_public_url() -> None:
    s = _ready()
    assert _derive(s).url == f"https://{HID}.homes.lumogis.app"
    assert _derive(s, https_port=8443).url == f"https://{HID}.homes.lumogis.app:8443"
    assert st.public_url(s, NOW, 443) == f"https://{HID}.homes.lumogis.app"
    # Error overlays don't hide a working address…
    assert st.public_url(_ready(last_error="worker_unreachable"), NOW, 443) is not None
    # …but an expired certificate or "off" does.
    assert st.public_url(_ready(cert_not_after=NOW - timedelta(seconds=1)), NOW, 443) is None
    assert st.public_url(_ready(enabled=False), NOW, 443) is None


def test_retry_after_seconds() -> None:
    s = _ready(registered_at=None, next_retry_at=NOW + timedelta(seconds=90))
    assert _derive(s).retry_after_seconds == 90


def test_save_is_0600_in_0700_dir(workspace) -> None:
    st.save(_ready())
    path = workspace / "home-dns" / "state.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert st.load().household_id == HID


def test_corrupt_state_is_quarantined(workspace) -> None:
    d = workspace / "home-dns"
    d.mkdir()
    (d / "state.json").write_text("{not json")
    s = st.load(NOW)
    assert s.last_error == "state_corrupt" and not s.enabled
    assert _derive(s).state == "error"  # row 0 wins over "off"
    names = sorted(p.name for p in d.iterdir())
    assert "state.json" in names
    assert any(n.startswith("state.json.corrupt-") for n in names)
    assert st.load(NOW).last_error == "state_corrupt"  # persisted, not lost on next load


# ----------------------------------------------------- LUM-996: "the name is new"

WINDOW = st.NAME_NEW_WINDOW


def test_name_new_window_turns_blocked_into_ready() -> None:
    anchor = NOW - timedelta(minutes=10)
    view = _derive(
        _ready(
            resolver="blocked",
            resolver_checked_at=NOW - timedelta(minutes=1),
            registered_at=anchor,
            name_published_at=anchor,
        )
    )
    assert view.state == "ready"
    assert view.name_new_until == anchor + timedelta(minutes=35)


def test_router_blocked_after_the_window() -> None:
    anchor = NOW - timedelta(minutes=46)
    view = _derive(
        _ready(
            resolver="blocked",
            resolver_checked_at=NOW - timedelta(minutes=5),  # after the window end (now-11m)
            registered_at=anchor,
            name_published_at=anchor,
        )
    )
    assert view.state == "router_blocked"
    assert view.name_new_until is None


def test_verdict_from_inside_the_window_does_not_block_after_it() -> None:
    anchor = NOW - timedelta(minutes=36)  # the window ended a minute ago
    view = _derive(
        _ready(
            resolver="blocked",
            resolver_checked_at=NOW - timedelta(minutes=6),
            registered_at=anchor,
            name_published_at=anchor,
        )
    )
    assert view.state == "ready"
    assert view.name_new_until is None


def test_verdict_checked_exactly_at_the_window_end_counts() -> None:
    anchor = NOW - timedelta(hours=1)
    view = _derive(
        _ready(
            resolver="blocked",
            resolver_checked_at=anchor + WINDOW,
            registered_at=anchor,
            name_published_at=anchor,
        )
    )
    assert view.state == "router_blocked"


def test_old_state_without_resolver_checked_at_is_ready() -> None:
    old = NOW - timedelta(hours=2)
    view = _derive(
        _ready(
            resolver="blocked", resolver_checked_at=None, registered_at=old, name_published_at=old
        )
    )
    assert view.state == "ready"


def test_window_anchors_on_the_later_of_registration_and_publication() -> None:
    view = _derive(
        _ready(
            registered_at=NOW - timedelta(hours=2),
            name_published_at=NOW - timedelta(minutes=5),
        )
    )
    assert view.state == "ready"
    assert view.name_new_until == NOW - timedelta(minutes=5) + WINDOW


def test_snippet_applied_at_is_not_an_anchor() -> None:
    old = NOW - timedelta(days=3)
    view = _derive(
        _ready(
            registered_at=old,
            name_published_at=old,
            snippet_applied_at=NOW - timedelta(minutes=1),
            resolver="blocked",
            resolver_checked_at=NOW - timedelta(minutes=1),
        )
    )
    assert view.state == "router_blocked"
    assert view.name_new_until is None


@pytest.mark.parametrize(
    "overrides,code",
    [
        (dict(cert_not_after=NOW - timedelta(seconds=1)), "renewal_failing"),
        (dict(last_error="caddy_snippet_rejected"), "caddy_snippet_rejected"),
        (
            dict(cert_not_after=None, snippet_applied_at=NOW - timedelta(minutes=16)),
            "certificate_pending",
        ),
    ],
)
def test_window_does_not_mask_errors(overrides, code) -> None:
    anchor = NOW - timedelta(minutes=5)
    view = _derive(_ready(registered_at=anchor, name_published_at=anchor, **overrides))
    assert (view.state, view.error_code) == ("error", code)


def test_name_new_until_none_when_off() -> None:
    anchor = NOW - timedelta(minutes=5)
    view = _derive(_ready(enabled=False, registered_at=anchor, name_published_at=anchor))
    assert view.state == "off" and view.name_new_until is None


def test_old_state_without_fingerprint_loads(workspace) -> None:
    import json

    st.save(_ready())
    data = json.loads(st.state_path().read_text())
    for key in ("snippet_fingerprint", "resolver_checked_at", "name_published_at"):
        data.pop(key, None)
    st.state_path().write_text(json.dumps(data))
    loaded = st.load()
    assert loaded.enabled and loaded.household_id == HID
    assert loaded.snippet_fingerprint is None
    assert loaded.resolver_checked_at is None and loaded.name_published_at is None
    assert not list(workspace.glob("home-dns/state.json.corrupt-*"))
