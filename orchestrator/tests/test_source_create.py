# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user source creation (LUM-839).

`POST /sources` was the only source verb still `require_admin`. `GET`, `PATCH`,
`DELETE` and `sync` were all already per-user and owner-scoped, so this was not a
coherent "sources are admin-owned" posture — it was a create-only anomaly, and
nothing in `docs/decisions/` argued for it.

Two guards arrive with the wider caller population, and they answer **different**
questions:

* the **limiter** bounds *burst* — detection performs several outbound fetches
  per call, and the preview path is deliberately exempt from the cap, so without
  it that path would have no bound at all;
* the **cap** bounds *accumulation* — feeds poll forever once created.

Neither substitutes for the other. The cap counts only the types this route
creates: IMAP mailboxes come from a different, uncapped route and are not shown
on `/me/sources`, so counting them would refuse a user over rows they can
neither see nor delete from the surface doing the refusing.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config


class _Store:
    """Records inserts and answers the cap's COUNT.

    `count` is what the COUNT query returns; `rows` captures what was inserted so
    a test can assert the row's `user_id` came from the caller rather than the
    body.
    """

    def __init__(self, count=0):
        self.count = count
        self.rows: list[tuple] = []
        self.count_params: list = []
        self.count_sql: list[str] = []

    def fetch_one(self, sql, params=None):
        if "COUNT(*)" in sql and "FROM sources" in sql:
            self.count_params.append(params)
            self.count_sql.append(sql)
            return {"n": self.count}
        return None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT INTO SOURCES"):
            self.rows.append(params)
        return None


def _client(user_id="member1", role="user"):
    from auth import UserContext
    from routes import signals as sig

    ctx = UserContext(user_id=user_id, role=role, allows_shared=True)
    app = FastAPI()
    app.include_router(sig.router)
    app.dependency_overrides[sig.require_user] = lambda: ctx
    return TestClient(app), sig


@pytest.fixture
def env(monkeypatch):
    """Quiet both consent arms and stub detection; clear the shared limiter.

    `_source_detect_limiter` is module-level, so without the clear the tenth
    request of a *previous* module bleeds a 429 into the first request here.
    """
    from routes import signals as sig

    from services import external_consumer_guard as ecg

    store = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(ecg, "has_folder_sorting_mailbox", lambda _u: False)
    monkeypatch.setattr(sig, "_paperless_overlap_matches", lambda _u: [])
    monkeypatch.setattr(
        sig,
        "_detect_source",
        # Shape copied from `services.signal_source_detection.detect_signal_source`
        # (`:32-37`), not invented. An earlier draft of this fake omitted
        # `feed_url` and every confirmed-create test died on a KeyError inside
        # the handler — the fake failing differently from the real collaborator.
        lambda url: {
            "source_type": "rss",
            "feed_url": url,
            "preview_items": [],
            "reachable": True,
        },
    )
    # `schedule_source` is imported INSIDE the handler, so it is not an
    # attribute of `routes.signals` — patch it where it is defined, which is
    # also where the call-time import resolves it.
    import signals.feed_monitor as fm

    monkeypatch.setattr(fm, "schedule_source", lambda *_a, **_k: None)
    sig._source_detect_limiter.clear()
    monkeypatch.delenv("LUMOGIS_MAX_SOURCES_PER_USER", raising=False)
    # CSRF Origin check fires before require_user when this is set (compose
    # .env / a prior test). Sibling source modules already clear it; without
    # this, `test_an_unauthenticated_caller_is_401` gets 403 origin mismatch
    # instead of 401 after ~5200 tests have run.
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    yield store
    sig._source_detect_limiter.clear()


def _post(client, **extra):
    return client.post("/sources", json={"url": "https://example.com/feed", **extra})


# --- the change itself -------------------------------------------------------


def test_a_member_can_create_a_source(env):
    """The whole point of LUM-839. A non-admin role must reach the handler."""
    client, _ = _client(role="user")
    resp = _post(client, confirm=True)
    assert resp.status_code in (200, 201), resp.text
    assert env.rows, "a confirmed create must insert"


def test_an_admin_can_still_create(env):
    """`require_user` admits admins — the change widens, it does not swap."""
    client, _ = _client(user_id="admin1", role="admin")
    assert _post(client, confirm=True).status_code in (200, 201)


def test_the_created_source_belongs_to_the_caller_not_the_body(env):
    """Attribution must be unforgeable.

    No body field feeds `user_id`; it comes from `UserContext`. This asserts the
    property rather than the absence of a field, so it still holds if someone
    later adds an owner-shaped field to `SourceRequest`.
    """
    client, _ = _client(user_id="member1")
    resp = client.post(
        "/sources",
        json={
            "url": "https://example.com/feed",
            "confirm": True,
            "user_id": "someone-else",
            "owner": "someone-else",
        },
    )
    assert resp.status_code in (200, 201, 422), resp.text
    if env.rows:
        assert "member1" in env.rows[0]
        assert "someone-else" not in env.rows[0]


def test_an_unauthenticated_caller_is_401(env, monkeypatch):
    """The DoD lists 401 and nothing here asserted it.

    Built as a real dependency resolution rather than an override: with
    `AUTH_ENABLED=true` and no credentials, `require_user` must refuse. An
    override-based client would bypass the very dependency under test.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import signals as sig

    monkeypatch.setenv("AUTH_ENABLED", "true")
    app = FastAPI()
    app.include_router(sig.router)
    client = TestClient(app)
    resp = client.post("/sources", json={"url": "https://example.com/feed", "confirm": True})
    assert resp.status_code == 401, resp.text


# --- the cap -----------------------------------------------------------------


def test_the_hundredth_source_is_allowed(env):
    env.count = 99
    client, _ = _client()
    assert _post(client, confirm=True).status_code in (200, 201)


def test_the_hundred_and_first_is_refused(env):
    env.count = 100
    client, _ = _client()
    resp = _post(client, confirm=True)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "source_cap_reached"


def test_the_cap_is_per_user_not_global(env):
    """The test that catches a `COUNT(*)` missing its `WHERE user_id`.

    A global count would refuse user B because user A is at the ceiling, and no
    single-user test can tell the difference.
    """
    env.count = 100
    client, _ = _client(user_id="member1")
    assert _post(client, confirm=True).status_code == 422
    assert env.count_params, "the cap must actually query"
    assert "member1" in env.count_params[0], "the COUNT must be scoped to the caller"


def test_the_cap_does_not_count_mailboxes(env):
    """Owner decision 2026-08-23: cap what this route creates.

    A user with 100 IMAP mailboxes and no feeds can still add a feed. Mailboxes
    come from `POST /me/email-sources` — uncapped, and invisible on
    `/me/sources` — so counting them would refuse someone over rows they cannot
    act on from the surface doing the refusing.

    Asserted at the query, because a fake returning a number cannot distinguish
    "counted mailboxes" from "did not".
    """
    env.count = 0
    client, _ = _client()
    assert _post(client, confirm=True).status_code in (200, 201)
    params = env.count_params[0]
    types = params[1]
    assert "imap" not in types
    assert set(types) == {"paperless", "rss", "page"}


def test_a_paused_source_still_counts_against_the_cap(env):
    """LUM-844 / ADR 217 section 5: the cap counts rows the user can act on, and a
    paused row is one (resume or remove it). If the COUNT filtered on ``active``,
    pausing would be a way round the cap.

    Asserted at the SQL: the fake returns a fixed count whatever the query says,
    so a refusal assertion here would prove nothing the 101st-source test does not.
    """
    env.count = 0
    client, _ = _client()
    assert _post(client, confirm=True).status_code in (200, 201)
    assert env.count_sql, "the cap must actually query"
    assert "active" not in env.count_sql[0].lower()


def test_the_paperless_path_is_capped_too(env):
    """The cap sits ABOVE the type branch, and this is what proves it.

    `add_or_preview_source` has two INSERTs — paperless and detect. A cap placed
    beside either one leaves the other uncapped, and every detect-path test
    still passes. The plan originally specified "immediately before the INSERT",
    singular, which would have shipped paperless uncapped.
    """
    env.count = 100
    client, _ = _client()
    resp = client.post(
        "/sources",
        json={"url": "https://paperless.lan", "source_type": "paperless", "confirm": True},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "source_cap_reached"


def test_a_preview_is_never_capped(env):
    """`body.confirm` is what exempts preview — not where the check sits.

    Refusing a preview teaches nothing: the user has not asked to keep anything
    yet. The limiter, not the cap, is what bounds this path.
    """
    env.count = 500
    client, _ = _client()
    assert _post(client, confirm=False).status_code == 200


def test_a_cap_query_failure_does_not_block_the_create(env, monkeypatch):
    """Fail open — the real store raises, it does not return a sentinel.

    A resource guard that cannot answer must not stand between a user and their
    document scanner.
    """

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(env, "fetch_one", _boom)
    client, _ = _client()
    assert _post(client, confirm=True).status_code in (200, 201)


def test_the_cap_failure_log_carries_no_exception_message(env, monkeypatch, caplog):
    """The log line names the exception TYPE, never its text.

    A psycopg error string can carry the failing statement and its parameters —
    which on this path means a user id in the log for a condition an operator
    can diagnose without one. Same posture as `sending_addresses` and
    `paperless_overlap.username_skip`.
    """
    import logging

    def _boom(*_a, **_k):
        raise RuntimeError("SELECT ... WHERE user_id = 'member1' -- secretish")

    monkeypatch.setattr(env, "fetch_one", _boom)
    client, _ = _client()
    with caplog.at_level(logging.WARNING):
        assert _post(client, confirm=True).status_code in (200, 201)
    assert "source_cap_check_failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "secretish" not in caplog.text
    assert "member1" not in caplog.text


def test_the_env_override_raises_the_ceiling(env, monkeypatch):
    """An install that upgraded already over the cap needs a way out."""
    monkeypatch.setenv("LUMOGIS_MAX_SOURCES_PER_USER", "250")
    env.count = 120
    client, _ = _client()
    assert _post(client, confirm=True).status_code in (200, 201)


def test_a_junk_env_value_falls_back_to_the_default(env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_MAX_SOURCES_PER_USER", "not-a-number")
    env.count = 100
    client, _ = _client()
    assert _post(client, confirm=True).status_code == 422


# --- the limiter -------------------------------------------------------------


def test_the_eleventh_request_in_a_minute_is_429(env):
    client, _ = _client()
    for _ in range(10):
        assert _post(client, confirm=False).status_code == 200
    resp = _post(client, confirm=False)
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "rate_limited"


def test_the_limiter_refuses_before_any_outbound_fetch(env, monkeypatch):
    """Position is the property, and a naive test cannot see it.

    A limiter placed *after* detection still returns 429 and still passes
    `test_the_eleventh_request_in_a_minute_is_429` — while having already made
    the outbound requests it exists to prevent.
    """
    from routes import signals as sig

    calls: list = []
    monkeypatch.setattr(
        sig,
        "_detect_source",
        lambda url: (
            calls.append(url)
            or {
                "source_type": "rss",
                "feed_url": url,
                "preview_items": [],
                "reachable": True,
            }
        ),
    )
    client, _ = _client()
    for _ in range(10):
        _post(client, confirm=False)
    before = len(calls)
    assert _post(client, confirm=False).status_code == 429
    assert len(calls) == before, "a refused request must not have fetched anything"


def test_the_limiter_is_per_user_not_global(env):
    client_a, _ = _client(user_id="member1")
    for _ in range(10):
        _post(client_a, confirm=False)
    assert _post(client_a, confirm=False).status_code == 429

    client_b, _ = _client(user_id="member2")
    assert _post(client_b, confirm=False).status_code == 200


# --- AUTH_ENABLED=false: the deployment most self-hosters run ----------------


def test_the_cap_applies_under_auth_disabled(env, monkeypatch):
    """Both guards are no-ops under AUTH-off, so the authz flip is invisible
    there — but the cap is NEW behaviour in that mode, keyed on the single
    shared `default` identity."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    env.count = 100
    client, _ = _client(user_id="default")
    assert _post(client, confirm=True).status_code == 422


def test_the_limiter_applies_under_auth_disabled(env, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client, _ = _client(user_id="default")
    for _ in range(10):
        _post(client, confirm=False)
    assert _post(client, confirm=False).status_code == 429


# --- the cross-language constant, pinned (verify-plan Step 4g) ---------------


def test_the_capped_types_match_the_types_the_screen_shows():
    """`_CAPPED_SOURCE_TYPES` and the web's `MANAGED_TYPES` are one idea in two
    languages, and a comment saying "do not drift" does not stop drift.

    If they diverge, the cap counts rows `/me/sources` does not display — which
    is precisely the failure the owner decision of 2026-08-23 was made to
    prevent. So it is pinned rather than asserted in prose.
    """
    import re
    from pathlib import Path

    from routes import signals as sig

    view = (
        Path(__file__).resolve().parents[2] / "clients/lumogis-web/src/features/me/SourcesView.tsx"
    )
    src = view.read_text()
    m = re.search(r"const MANAGED_TYPES = new Set\(\[([^\]]*)\]\)", src)
    assert m, (
        "MANAGED_TYPES not found — if it was renamed, re-point this test rather than deleting it"
    )
    web_types = set(re.findall(r'"([^"]+)"', m.group(1)))

    assert web_types == set(sig._CAPPED_SOURCE_TYPES), (
        f"web shows {sorted(web_types)} but the cap counts "
        f"{sorted(sig._CAPPED_SOURCE_TYPES)} — a user would be refused over rows "
        "the screen does not display"
    )
