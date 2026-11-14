# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-729 — the gate at both doors, end to end through the routes.

`test_external_consumer_guard.py` tests the predicate. This file tests that the
predicate is actually *reached*, from both arming points, and that everything
else still goes through untouched.

**Two doors, one test file, because one door passing proves nothing.** The
organize PATCH and `email_cleanup.approve()` are independent ways to arm the
identical starvation; gating one and leaving the other is how LUM-724's
three-share-doors problem happened, and a single test on either door passes
against that broken design.

The gate must also be *invisible* to everyone who does not run paperless. That is
most of these tests: a consent gate that leaked into the ordinary path would be a
worse regression than the bug it fixes, because it would hit every user rather
than the few who run both systems.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_auth_phase1 import FakeUsersStore

_SID = "11111111-1111-1111-1111-111111111111"
_CODE = "external_consumer_may_starve"


class _Store(FakeUsersStore):
    """One owned imap source, and a switch for whether paperless is configured."""

    def __init__(self, *, paperless: bool = True, folder_on: bool = False):
        super().__init__()
        self.paperless = paperless
        self.folder_on = folder_on
        self.updates: list[tuple] = []
        self.paperless_lookups = 0

    @staticmethod
    def _norm(q):
        return " ".join(q.split()).lower()

    def fetch_one(self, query, params=None):
        q = self._norm(query)
        p = params or ()
        if "source_type = 'paperless'" in q:
            self.paperless_lookups += 1
            return {"present": 1} if self.paperless else None
        if "id::text as id" in q and "from sources" in q and "source_type = 'imap'" in q:
            sid, uid = p
            if sid != _SID or uid != "default":
                return None
            return {"id": _SID, "folder_on": self.folder_on}
        return super().fetch_one(query, params)

    def execute(self, query, params=None):
        q = self._norm(query)
        if "update sources set organize_label_enabled" in q:
            self.updates.append(params)
            return
        return super().execute(query, params)


@pytest.fixture
def env(monkeypatch):
    import config as _config

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)

    def _install(**kwargs):
        store = _Store(**kwargs)
        _config._instances["metadata_store"] = store
        return store

    yield _install
    _config._instances.pop("metadata_store", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def _patch_organize(source_id: str, **body):
    payload = {"organize_label": False, "organize_folder": False}
    payload.update(body)
    with _client() as client:
        return client.patch(f"/me/email-sources/{source_id}/organize", json=payload)


# --- door 1: the organize route ---------------------------------------------


def test_turning_folder_sort_on_with_paperless_is_refused(env):
    store = env(paperless=True, folder_on=False)

    resp = _patch_organize(_SID, organize_folder=True, organize_folder_categories=["receipts"])

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == _CODE
    assert store.updates == [], "the refusal must not have written anything"


def test_the_refusal_names_the_consequence_not_just_a_code(env):
    """The bug is that the consequence was invisible. A bare error code would
    reproduce it in words."""
    env(paperless=True)

    detail = _patch_organize(
        _SID, organize_folder=True, organize_folder_categories=["receipts"]
    ).json()["detail"]

    assert "paperless" in detail["message"].lower()
    assert "inbox" in detail["message"].lower()
    assert detail["alternative"] and detail["recovery"]


def test_the_acknowledgement_lets_it_through(env):
    store = env(paperless=True)

    resp = _patch_organize(
        _SID,
        organize_folder=True,
        organize_folder_categories=["receipts"],
        acknowledge_external_consumer=True,
    )

    assert resp.status_code == 202, resp.text
    assert len(store.updates) == 1


def test_an_OMITTED_acknowledgement_refuses(env):
    """**The default is the contract.** The gate is only meaningful if an unaware
    client — one written before this field existed — is refused rather than waved
    through."""
    env(paperless=True)

    resp = _patch_organize(_SID, organize_folder=True, organize_folder_categories=["receipts"])

    assert resp.status_code == 422


def test_apply_to_existing_on_an_ALREADY_sorting_source_is_gated(env):
    """**Mutation target.** The retroactive bulk move over the whole mailbox is
    the single most destructive request this route accepts, and under a
    transition-only gate it passes through untouched."""
    store = env(paperless=True, folder_on=True)

    resp = _patch_organize(
        _SID,
        organize_folder=True,
        organize_folder_categories=["receipts"],
        apply_to_existing=True,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == _CODE
    assert "already" in resp.json()["detail"]["message"].lower()
    assert store.updates == []


def test_editing_categories_while_already_sorting_is_NOT_gated(env):
    """**Mutation target for the other clause.** The body is a full replace, so
    reading the value rather than the change would toll this user on every save
    — and a gate that fires constantly is one people learn to click through."""
    store = env(paperless=True, folder_on=True)

    resp = _patch_organize(
        _SID, organize_folder=True, organize_folder_categories=["receipts", "bills"]
    )

    assert resp.status_code == 202, resp.text
    assert len(store.updates) == 1


def test_turning_folder_sort_OFF_is_never_gated(env):
    """Reducing the harm must never be obstructed — least of all by the guard
    that exists because of it."""
    store = env(paperless=True, folder_on=True)

    resp = _patch_organize(_SID, organize_folder=False)

    assert resp.status_code == 202, resp.text
    assert len(store.updates) == 1


def test_turning_labels_on_is_never_gated(env):
    """Labels do not move mail, so paperless still receives everything. This is
    the alternative the refusal offers; gating it would leave the user with no
    unblocked option at all."""
    store = env(paperless=True)

    resp = _patch_organize(_SID, organize_label=True)

    assert resp.status_code == 202, resp.text
    assert len(store.updates) == 1


def test_no_paperless_source_means_no_behaviour_change_whatsoever(env):
    store = env(paperless=False)

    resp = _patch_organize(_SID, organize_folder=True, organize_folder_categories=["receipts"])

    assert resp.status_code == 202, resp.text
    assert len(store.updates) == 1


def test_the_paperless_lookup_is_skipped_when_nothing_is_being_armed(env):
    """No query cost on the ordinary path. The existence check runs only when the
    request could actually cause the harm."""
    store = env(paperless=True, folder_on=True)

    _patch_organize(_SID, organize_folder=True, organize_folder_categories=["receipts"])

    assert store.paperless_lookups == 0


def test_the_paperless_lookup_is_skipped_when_already_acknowledged(env):
    """Ordering: cheapest checks first. An acknowledged request needs no query to
    decide anything."""
    store = env(paperless=True)

    _patch_organize(
        _SID,
        organize_folder=True,
        organize_folder_categories=["receipts"],
        acknowledge_external_consumer=True,
    )

    assert store.paperless_lookups == 0


def test_another_users_source_is_still_404_not_422(env):
    """Ownership before consent. Leaking "this mailbox exists and here is what
    would happen to it" to a non-owner would be worse than the bug."""
    env(paperless=True)

    resp = _patch_organize("22222222-2222-2222-2222-222222222222", organize_folder=True)

    assert resp.status_code == 404


# --- door 2: the cleanup approval -------------------------------------------


def test_approving_a_moving_cleanup_with_paperless_is_refused(env, monkeypatch):
    """**The second arming point.** `approve()` schedules rather than performs the
    move, but it is the sole writer of `'executing'` and therefore the moment of
    consent — which is where a consent gate belongs."""
    from services import email_cleanup
    from services import external_consumer_guard as ecg

    monkeypatch.setattr(ecg, "has_external_consumer", lambda uid: True)
    monkeypatch.setattr(
        email_cleanup, "_owned_job", lambda ms, j, u, s: {"id": j, "state": "ready"}
    )
    env(paperless=True)
    executed: list = []
    monkeypatch.setattr(
        email_cleanup.config,
        "get_metadata_store",
        lambda: type("S", (), {"execute": lambda self, *a: executed.append(a)})(),
    )

    with pytest.raises(email_cleanup.CleanupError) as exc:
        email_cleanup.approve("j1", "default", _SID, [{"category": "receipts", "move": True}])

    assert exc.value.code == _CODE
    assert exc.value.status == 422
    assert executed == [], "the refusal must not have scheduled anything"


def test_the_cleanup_acknowledgement_lets_it_through(env, monkeypatch):
    from services import email_cleanup
    from services import external_consumer_guard as ecg

    monkeypatch.setattr(ecg, "has_external_consumer", lambda uid: True)
    monkeypatch.setattr(
        email_cleanup, "_owned_job", lambda ms, j, u, s: {"id": j, "state": "ready"}
    )
    executed: list = []
    monkeypatch.setattr(
        email_cleanup.config,
        "get_metadata_store",
        lambda: type("S", (), {"execute": lambda self, *a: executed.append(a)})(),
    )

    out = email_cleanup.approve(
        "j1",
        "default",
        _SID,
        [{"category": "receipts", "move": True}],
        acknowledge_external_consumer=True,
    )

    assert out == {"approved": 1}
    assert len(executed) == 1


def test_approving_NOTHING_MOVABLE_is_not_gated(env, monkeypatch):
    """An approval that moves no mail cannot starve anything. Refusing it would
    be a warning about a consequence that is not going to happen."""
    from services import email_cleanup
    from services import external_consumer_guard as ecg

    called = []
    monkeypatch.setattr(ecg, "has_external_consumer", lambda uid: called.append(uid) or True)
    monkeypatch.setattr(
        email_cleanup, "_owned_job", lambda ms, j, u, s: {"id": j, "state": "ready"}
    )
    monkeypatch.setattr(
        email_cleanup.config,
        "get_metadata_store",
        lambda: type("S", (), {"execute": lambda self, *a: None})(),
    )

    out = email_cleanup.approve("j1", "default", _SID, [{"category": "receipts", "move": False}])

    assert out == {"approved": 0}
    assert called == [], "no query on a request that moves nothing"


def test_the_cleanup_route_surfaces_the_full_body_not_just_the_code(env, monkeypatch):
    """`_cleanup_or_http` used to flatten every CleanupError to `{"code": ...}`.
    A refusal the user is expected to ACT on has to arrive with the copy attached,
    or the web has nothing to show."""
    from services import email_cleanup
    from services import external_consumer_guard as ecg

    def _boom(*a, **k):
        raise email_cleanup.CleanupError(_CODE, 422, detail=ecg.refusal_detail(ecg.ARM_BULK_MOVE))

    monkeypatch.setattr(email_cleanup, "approve", _boom)
    env(paperless=True)

    with _client() as client:
        resp = client.post(
            f"/me/email-sources/{_SID}/cleanup/j1/approve",
            json={"actions": [{"category": "receipts", "move": True}]},
        )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == _CODE
    assert "paperless" in detail["message"].lower()


def test_an_ORDINARY_cleanup_error_is_still_the_bare_code(env, monkeypatch):
    """The rest are diagnostics, not decisions, and their shape is a contract the
    web already depends on."""
    from services import email_cleanup

    def _boom(*a, **k):
        raise email_cleanup.CleanupError("job_not_ready", 409)

    monkeypatch.setattr(email_cleanup, "approve", _boom)
    env(paperless=True)

    with _client() as client:
        resp = client.post(
            f"/me/email-sources/{_SID}/cleanup/j1/approve",
            json={"actions": [{"category": "receipts", "move": True}]},
        )

    assert resp.status_code == 409
    assert resp.json()["detail"] == {"code": "job_not_ready"}


# --- both doors share one helper ---------------------------------------------


def test_both_call_sites_use_the_SAME_guard_module():
    """Structural. Two copies of this predicate would drift, and the copy would
    drift with them — the user would be told two different stories about one
    consequence."""
    import inspect

    from routes import signals as sroutes

    from services import email_cleanup

    route_src = inspect.getsource(sroutes.set_email_organize)
    approve_src = inspect.getsource(email_cleanup.approve)

    assert "external_consumer_guard" in route_src
    assert "external_consumer_guard" in approve_src


# --- door 3: connecting paperless while a mailbox already folder-sorts -------
#
# The mirror arming event, and the one the first pass missed entirely. The plan
# reasoned about arming organize WITH paperless present and never about arming
# paperless WITH organize present — identical harm, and Lumogis-first is at least
# as likely an ordering. Found in code review after the rest had shipped.


class _PaperlessCreateStore:
    def __init__(self, *, folder_sorting: bool):
        self.folder_sorting = folder_sorting
        self.inserts: list[tuple] = []

    def fetch_one(self, query, params=None):
        q = " ".join(query.split()).lower()
        if "organize_folder_enabled = true" in q:
            return {"present": 1} if self.folder_sorting else None
        return None

    def execute(self, query, params=None):
        self.inserts.append(params)


def _create_paperless(store, monkeypatch, **body_kwargs):
    from auth import UserContext
    from routes import signals as sroutes

    import config as _config

    monkeypatch.setattr(_config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(sroutes, "_register_source_poll", lambda *a, **k: None, raising=False)
    payload = {"url": "https://paperless.home.lan", "confirm": True, "source_type": "paperless"}
    payload.update(body_kwargs)
    return sroutes.add_or_preview_source(
        sroutes.SourceRequest(**payload), user=UserContext(user_id="default")
    )


def test_connecting_paperless_while_folder_sorting_is_refused(monkeypatch):
    from fastapi import HTTPException

    store = _PaperlessCreateStore(folder_sorting=True)

    with pytest.raises(HTTPException) as exc:
        _create_paperless(store, monkeypatch)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == _CODE
    assert store.inserts == [], "the refusal must not have created the source"


def test_the_connect_refusal_names_the_fix_available_to_THIS_user(monkeypatch):
    """The other two messages tell an organize user about paperless. Here the
    user is setting paperless up, and the lever they hold is the mailbox
    setting."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _create_paperless(_PaperlessCreateStore(folder_sorting=True), monkeypatch)

    assert "folder" in exc.value.detail["message"].lower()


def test_the_acknowledgement_connects_it(monkeypatch):
    store = _PaperlessCreateStore(folder_sorting=True)

    _create_paperless(store, monkeypatch, acknowledge_external_consumer=True)

    assert len(store.inserts) == 1


def test_connecting_paperless_with_no_folder_sorting_mailbox_is_untouched(monkeypatch):
    store = _PaperlessCreateStore(folder_sorting=False)

    _create_paperless(store, monkeypatch)

    assert len(store.inserts) == 1


def test_the_PREVIEW_step_is_never_gated(monkeypatch):
    """`confirm=false` writes nothing and cannot starve anything. Refusing it
    would stop the user seeing what they were about to connect — a warning that
    fires before there is anything to warn about."""
    store = _PaperlessCreateStore(folder_sorting=True)

    out = _create_paperless(store, monkeypatch, confirm=False)

    assert out["source_type"] == "paperless"
    assert store.inserts == []


# --- the empty-category save is gated ON PURPOSE, and must stay that way -----


def test_enabling_folder_sort_with_NO_categories_is_still_gated(env):
    """**Counter-intuitive and load-bearing.**

    `organize_folder: true` with an empty category list moves nothing today, so
    this looks like a false positive a future reader would "fix".

    It is not. Adding categories afterwards is an already-on edit, which is
    deliberately NOT gated (D1) — so this save is the ONLY moment the user is
    ever asked. Tightening the predicate here would silently remove the gate for
    anyone who enables first and picks categories second.
    """
    store = env(paperless=True, folder_on=False)

    resp = _patch_organize(_SID, organize_folder=True)

    assert resp.status_code == 422, resp.text
    assert store.updates == []
