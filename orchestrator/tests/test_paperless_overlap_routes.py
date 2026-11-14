# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-744/835/836 — the batch route and the connect-time arm.

Two things here are deliberately awkward to write, and both exist because review
found the same defect shape three times on this feature: **a surface that runs,
returns cleanly, and shows the user nothing.**

* `test_connect_refusal_is_not_parsed_by_the_starvation_matcher` asserts a
  *negative* about a client module. The earlier design sent the overlap arm's
  refusal through `externalConsumerWarning.ts`, which hard-matches
  `STARVATION_CODE` — so the refusal would have arrived and rendered nothing.
  Nothing on the server side detects that; only pinning the codes apart does.
* `test_all_is_scoped_to_rule_bearing_mailboxes` asserts an *absence*. A mailbox
  with no filing rules cannot duplicate anything, so probing it is cost with no
  answer — and the natural implementation (enumerate every mailbox) passes every
  positive assertion.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.api_v1 import email_attachment_ingest as mod

import config
from services import external_consumer_guard as ecg
from services import paperless_overlap as po

WORK = "11111111-1111-1111-1111-111111111111"
HOME = "22222222-2222-2222-2222-222222222222"
#: Owned by somebody else. Must be absent from the response, never a 403 — a
#: refusal would confirm the id exists, which is an enumeration oracle.
STRANGER = "33333333-3333-3333-3333-333333333333"

NAMES = {WORK: "Work", HOME: "Personal"}


class _Store:
    def __init__(self, owned=None):
        self.owned = NAMES if owned is None else owned

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        if "FROM sources" in sql:
            ids = (params or (None, []))[1] or []
            return [{"id": i, "name": self.owned[i]} for i in ids if i in self.owned]
        return []

    def execute(self, sql, params=None):
        return None


def _client(user_id="u1"):
    from auth import UserContext

    ctx = UserContext(user_id=user_id, role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    app.dependency_overrides[mod.require_same_origin] = lambda: None
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    """Each test gets its own bucket; otherwise the 20/60s limiter leaks between
    tests and the failure looks like a route bug."""
    from rate_limit import RequestRateLimiter

    monkeypatch.setattr(
        mod, "_overlap_limiter", RequestRateLimiter(max_requests=20, window_seconds=60)
    )


@pytest.fixture
def store(monkeypatch):
    st = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    return st


def _rules(monkeypatch, counts: dict):
    rows = []
    for sid, n in counts.items():
        rows.extend({"id": f"r{sid}{i}", "source_id": sid} for i in range(n))
    monkeypatch.setattr(mod.rules_svc, "_rules_for", lambda _u: rows)


def _overlaps(monkeypatch, mapping):
    monkeypatch.setattr(
        mod.overlap_svc,
        "overlap_for_sources",
        lambda _u, ids, **_k: {i: mapping[i] for i in ids if i in mapping},
    )


OK_HIGH = po.OverlapResult(status="ok", overlap=po.Overlap(confidence="high", action=3))
OK_LOW = po.OverlapResult(status="ok", overlap=po.Overlap(confidence="low"))
CLEAR = po.OverlapResult(status="ok")
DOWN = po.OverlapResult(status="unavailable")


# ── GET /attachment-ingest/paperless-overlap/all ─────────────────────────────


def test_all_returns_one_item_per_rule_bearing_mailbox(store, monkeypatch):
    _rules(monkeypatch, {WORK: 3, HOME: 1})
    _overlaps(monkeypatch, {WORK: OK_HIGH, HOME: CLEAR})

    body = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all").json()

    by_id = {i["source_id"]: i for i in body["items"]}
    assert set(by_id) == {WORK, HOME}
    assert by_id[WORK]["overlap"] is True
    assert by_id[WORK]["rule_count"] == 3
    assert by_id[WORK]["source_name"] == "Work"
    assert by_id[HOME]["overlap"] is False


def test_all_is_scoped_to_rule_bearing_mailboxes(store, monkeypatch):
    """HOME is owned and real, but has no filing rules — so it cannot duplicate
    anything and must not be probed or reported."""
    _rules(monkeypatch, {WORK: 1})
    _overlaps(monkeypatch, {WORK: OK_HIGH, HOME: OK_HIGH})

    body = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all").json()

    assert [i["source_id"] for i in body["items"]] == [WORK]


def test_no_rules_at_all_makes_no_probe(store, monkeypatch):
    """The free path: most users have no filing rules and must cost nothing."""
    _rules(monkeypatch, {})
    called: list = []
    monkeypatch.setattr(
        mod.overlap_svc, "overlap_for_sources", lambda *a, **k: called.append(a) or {}
    )

    body = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all").json()

    assert body == {"items": []}
    assert called == [], "no rules ⇒ no overlap probe at all"


def test_all_omits_mailboxes_the_caller_does_not_own(store, monkeypatch):
    """Absent, not 403. A refusal would confirm the id exists."""
    _rules(monkeypatch, {WORK: 1, STRANGER: 5})
    _overlaps(monkeypatch, {WORK: OK_HIGH, STRANGER: OK_HIGH})

    resp = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all")

    assert resp.status_code == 200
    assert [i["source_id"] for i in resp.json()["items"]] == [WORK]


def test_all_never_leaks_the_folder(store, monkeypatch):
    _rules(monkeypatch, {WORK: 1})
    _overlaps(
        monkeypatch,
        {
            WORK: po.OverlapResult(
                status="ok", overlap=po.Overlap(confidence="high", folder="INBOX")
            )
        },
    )

    body = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all").text

    assert "folder" not in body


def test_all_reports_unavailable_rather_than_clear(store, monkeypatch):
    """The invariant every surface depends on. `status` must survive so the
    client can stay silent instead of implying all-clear."""
    _rules(monkeypatch, {WORK: 1})
    _overlaps(monkeypatch, {WORK: DOWN})

    item = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all").json()["items"][0]

    assert item["status"] == "unavailable"
    assert item["overlap"] is False
    assert item["message"] is None


def test_all_sets_no_store(store, monkeypatch):
    _rules(monkeypatch, {WORK: 1})
    _overlaps(monkeypatch, {WORK: CLEAR})

    resp = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all")

    assert resp.headers["Cache-Control"] == "no-store"


def test_all_is_rate_limited(store, monkeypatch):
    _rules(monkeypatch, {WORK: 1})
    _overlaps(monkeypatch, {WORK: CLEAR})
    client = _client()

    codes = {
        client.get("/api/v1/email/attachment-ingest/paperless-overlap/all").status_code
        for _ in range(21)
    }

    assert 429 in codes


# ── context: the copy the caller actually gets ───────────────────────────────


def test_all_defaults_to_present_tense(store, monkeypatch):
    """`/all`'s consumer is the rules page, where the rules already exist."""
    _rules(monkeypatch, {WORK: 1})
    _overlaps(monkeypatch, {WORK: OK_LOW})

    item = _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all").json()["items"][0]

    assert item["message"] == po.ADVISORY_EXISTING


@pytest.mark.parametrize(
    ("result", "context", "expected"),
    [
        (OK_LOW, "creating", po.ADVISORY),
        (OK_LOW, "existing", po.ADVISORY_EXISTING),
        # `_ACTION_DELETE == 1`; OK_HIGH carries action=3, which is not DELETE.
        (OK_HIGH, "existing", po.MESSAGE_EXISTING),
        (
            po.OverlapResult(status="ok", overlap=po.Overlap(confidence="high", action=1)),
            "existing",
            po.MESSAGE_EXISTING_DELETE,
        ),
    ],
)
def test_context_selects_the_tense_and_the_delete_variant(
    store, monkeypatch, result, context, expected
):
    """Every `_EXISTING` constant shipped once with no caller passing
    `context=existing`, leaving all three dead. This is what proves they are
    reachable."""
    _rules(monkeypatch, {WORK: 1})
    _overlaps(monkeypatch, {WORK: result})

    item = (
        _client()
        .get(f"/api/v1/email/attachment-ingest/paperless-overlap/all?context={context}")
        .json()["items"][0]
    )

    assert item["message"] == expected


def test_single_source_route_is_unchanged_by_default(store, monkeypatch):
    """The shipped contract: same keys, same copy, no new `source_id`."""
    monkeypatch.setattr(mod, "_owned_imap_source", lambda *_a: {"id": WORK})
    monkeypatch.setattr(mod.overlap_svc, "overlap_for_source", lambda *_a: OK_LOW)

    body = (
        _client().get(f"/api/v1/email/attachment-ingest/paperless-overlap?source_id={WORK}").json()
    )

    assert set(body) == {"overlap", "confidence", "status", "message"}
    assert body["message"] == po.ADVISORY


# ── the connect-time arm's refusal body ──────────────────────────────────────


def test_connect_refusal_is_not_parsed_by_the_starvation_matcher():
    """The R2 defect, pinned. `externalConsumerWarning.ts` is
    `if (d.code !== STARVATION_CODE) return null`, so an overlap refusal wearing
    the starvation code would render the *wrong* warning, and one wearing a
    third new code would render nothing. It must be `OVERLAP_CODE`, which
    `paperlessOverlapWarning.ts` already matches."""
    detail = po.connect_refusal_detail([{"source_name": "Work", "rule_count": 2}])

    assert detail["code"] == po.OVERLAP_CODE
    assert detail["code"] != ecg.STARVATION_CODE
    assert ecg.refusal_detail(ecg.ARM_CONNECT_PAPERLESS)["code"] == ecg.STARVATION_CODE


def test_the_two_arms_carry_different_codes():
    """They warn about opposite harms. One code for both would make them
    indistinguishable to the client that has to render them differently."""
    assert (
        po.connect_refusal_detail([])["code"]
        != ecg.refusal_detail(ecg.ARM_CONNECT_PAPERLESS)["code"]
    )


# ── POST /sources — the connect-time arm (LUM-835) ───────────────────────────
#
# The design this replaced hooked a *credential* save. `has_external_consumer`
# reads the `sources` table, and a credential PUT writes no such row, so that
# notice would have short-circuited to "no paperless" forever. These tests are on
# the route that actually creates the row.


def _sources_client(user_id="admin1"):
    from auth import UserContext
    from routes import signals as sig

    ctx = UserContext(user_id=user_id, role="admin", allows_shared=True)
    app = FastAPI()
    app.include_router(sig.router)
    # LUM-839: POST /sources is require_user now. Overriding require_admin here
    # would be a SILENT no-op — the route no longer depends on it, so the test
    # would keep passing while no longer controlling the identity it sets.
    app.dependency_overrides[sig.require_user] = lambda: ctx
    return TestClient(app), sig


def _connect(client, **extra):
    body = {
        "url": "https://paperless.lan",
        "source_type": "paperless",
        "confirm": True,
        **extra,
    }
    return client.post("/sources", json=body)


@pytest.fixture
def connect(monkeypatch):
    client, sig = _sources_client()
    # The starvation arm is not under test here; keep it quiet so a failure is
    # unambiguously the overlap arm.
    monkeypatch.setattr(ecg, "has_folder_sorting_mailbox", lambda _u: False)
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())
    return client, sig


def test_connect_refuses_on_high_overlap(connect, monkeypatch):
    client, sig = connect
    monkeypatch.setattr(
        sig, "_paperless_overlap_matches", lambda _u: [{"source_name": "Work", "rule_count": 3}]
    )

    resp = _connect(client)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == po.OVERLAP_CODE
    assert detail["mailboxes"] == [{"source_name": "Work", "rule_count": 3}]


def test_connect_allows_when_the_overlap_is_acknowledged(connect, monkeypatch):
    client, sig = connect
    monkeypatch.setattr(
        sig, "_paperless_overlap_matches", lambda _u: [{"source_name": "Work", "rule_count": 3}]
    )

    resp = _connect(client, acknowledge_paperless_overlap=True)

    assert resp.status_code != 422


def test_acknowledging_starvation_does_NOT_acknowledge_overlap(connect, monkeypatch):
    """The reason these are two fields and not one.

    A user shown the starvation dialogue and told "yes, go ahead" has answered a
    question about paperless missing mail. They have not been told anything about
    documents being filed twice, and must still be asked.
    """
    client, sig = connect
    monkeypatch.setattr(
        sig, "_paperless_overlap_matches", lambda _u: [{"source_name": "Work", "rule_count": 1}]
    )

    resp = _connect(client, acknowledge_external_consumer=True)

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == po.OVERLAP_CODE


def test_acknowledging_overlap_does_NOT_acknowledge_starvation(connect, monkeypatch):
    """And the same in the other direction."""
    client, sig = connect
    monkeypatch.setattr(ecg, "has_folder_sorting_mailbox", lambda _u: True)
    monkeypatch.setattr(sig, "_paperless_overlap_matches", lambda _u: [])

    resp = _connect(client, acknowledge_paperless_overlap=True)

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == ecg.STARVATION_CODE


def test_connect_does_not_refuse_when_there_is_no_high_match(connect, monkeypatch):
    """ADR-192 D3: `low` never gates, and `_paperless_overlap_matches` filters to
    `high` before returning, so an empty list is the only "do not gate" signal."""
    client, sig = connect
    monkeypatch.setattr(sig, "_paperless_overlap_matches", lambda _u: [])

    assert _connect(client).status_code != 422


def test_connect_preview_is_never_gated(connect, monkeypatch):
    """`confirm=false` writes nothing and cannot duplicate anything. Refusing it
    would stop the user seeing what they were about to connect — the same
    reasoning the shipped starvation arm already applies."""
    client, sig = connect
    called: list = []
    monkeypatch.setattr(
        sig,
        "_paperless_overlap_matches",
        lambda _u: called.append(1) or [{"source_name": "W", "rule_count": 9}],
    )

    resp = client.post(
        "/sources",
        json={"url": "https://paperless.lan", "source_type": "paperless", "confirm": False},
    )

    assert resp.status_code == 200
    assert called == [], "the preview must not even run the probe"


def test_connect_survives_an_unreachable_paperless(connect, monkeypatch):
    """Fail open. A user who cannot connect their scanner because a *warning*
    could not be computed would rightly call the product broken."""
    client, sig = connect
    from services import email_attachment_rules as rules_svc

    monkeypatch.setattr(rules_svc, "_rules_for", lambda _u: [{"id": "r1", "source_id": WORK}])

    def _boom(*_a, **_k):
        raise RuntimeError("paperless is down")

    monkeypatch.setattr(po, "overlap_for_sources", _boom)

    resp = _connect(client)

    assert resp.status_code != 422, "the advisory being unavailable must not block a connect"


# ── the shared predicate (verify-plan Step 4g dedupe) ────────────────────────
#
# The first cut shipped this query and this grouping TWICE — once in the route,
# once in the connect gate. Two copies of a scoping predicate is how one of them
# later stops matching the other, and the symptom would be a mailbox warned about
# on one surface and silent on the other.


def test_rule_bearing_mailboxes_returns_names_and_counts(store, monkeypatch):
    from services import email_attachment_rules as rules_svc

    _rules(monkeypatch, {WORK: 2, HOME: 1})

    out = rules_svc.rule_bearing_mailboxes("u1")

    assert out == {
        WORK: {"source_name": "Work", "rule_count": 2},
        HOME: {"source_name": "Personal", "rule_count": 1},
    }


def test_rule_bearing_mailboxes_omits_unowned_and_ruleless(store, monkeypatch):
    from services import email_attachment_rules as rules_svc

    # STRANGER has rules but is not in the caller's `sources`; HOME is owned but
    # has no rules and so is never asked about.
    _rules(monkeypatch, {WORK: 1, STRANGER: 4})

    assert list(rules_svc.rule_bearing_mailboxes("u1")) == [WORK]


def test_no_rules_reads_no_sources_at_all(monkeypatch):
    """The free path, at the helper rather than the route: the cached rules read
    answers it, so a user with no filing rules costs no `sources` query."""
    from services import email_attachment_rules as rules_svc

    st = _Store()
    monkeypatch.setattr(config, "get_metadata_store", lambda: st)
    monkeypatch.setattr(rules_svc, "_rules_for", lambda _u: [])
    queried: list = []
    st.fetch_all = lambda sql, params=None: queried.append(sql) or []

    assert rules_svc.rule_bearing_mailboxes("u1") == {}
    assert queried == []


def test_both_surfaces_use_the_same_predicate(store, monkeypatch):
    """The route and the connect gate must agree on which mailboxes count."""
    from routes import signals as sig

    from services import email_attachment_rules as rules_svc

    _rules(monkeypatch, {WORK: 2})
    seen: list = []
    real = rules_svc.rule_bearing_mailboxes
    monkeypatch.setattr(
        rules_svc,
        "rule_bearing_mailboxes",
        lambda uid: seen.append("called") or real(uid),
    )
    _overlaps(monkeypatch, {WORK: CLEAR})
    monkeypatch.setattr(po, "overlap_for_sources", lambda *_a, **_k: {WORK: CLEAR})

    _client().get("/api/v1/email/attachment-ingest/paperless-overlap/all")
    sig._paperless_overlap_matches("u1")

    assert len(seen) == 2, "both surfaces must go through the one helper"


def test_all_requires_authentication(store, monkeypatch):
    """No `require_user` override: the real dependency must refuse.

    Every other test here overrides `require_user` to get a caller, which means
    none of them prove the route is guarded at all. This one drives the real
    dependency down its `AUTH_ENABLED=true` path with an anonymous context —
    following `test_admin_unshare_route_authz._auth_as`, which is the shipped
    pattern for this.

    Under `AUTH_ENABLED=false` the same call is a **200**, and correctly so:
    single-user self-hosted is an unauthenticated deployment and `require_user`
    resolves a default user there. Asserting 401 without forcing the flag would
    have been asserting the harness, not the route.
    """
    import authz
    from auth import UserContext

    _rules(monkeypatch, {WORK: 1})
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    # An anonymous context, not `None`: `require_user` reads `.is_authenticated`
    # on whatever `get_user` returns, so `None` raises an AttributeError instead
    # of the 401 under test — which would pass a naive `!= 200` assertion while
    # proving nothing.
    anon = UserContext(user_id="", role="", allows_shared=False)
    monkeypatch.setattr(authz, "get_user", lambda request: anon)

    def _boom(*a, **k):
        pytest.fail("the overlap probe ran despite an unauthenticated caller")

    monkeypatch.setattr(mod.overlap_svc, "overlap_for_sources", _boom)

    app = FastAPI()
    app.include_router(mod.router)
    resp = TestClient(app).get("/api/v1/email/attachment-ingest/paperless-overlap/all")

    assert resp.status_code == 401
