# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/me/shell`` — the navigation shell's state (LUM-993).

Every source is stubbed at the service seam the route reads, keyed by user id
where the real source is per-user, so the isolation tests can fail.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from models.privacy_mode import InstancePrivacyMode

URL = "/api/v1/me/shell"


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _hdr(monkeypatch: pytest.MonkeyPatch, user_id: str = "alice") -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-me-shell-access-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id, 'user')}"}


def _svc(scopes: list[str], healthy: bool = True):
    return SimpleNamespace(
        healthy=healthy, manifest=SimpleNamespace(permissions_required=scopes, id="cap")
    )


class World:
    """Per-user stubbed sources; everything defaults to 'nothing'."""

    def __init__(self) -> None:
        self.imap: dict[str, bool] = {}
        self.mailboxes: dict[str, dict[str, str]] = {}
        self.services: list = []
        self.approvals: dict[str, int] = {}
        self.proposals: dict[str, int] = {}
        self.recoverable: dict[str, int] = {}
        self.remote_allowed: dict[str, bool] = {}
        self.graph = "disabled"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    monkeypatch.setenv("STT_BACKEND", "none")

    from auth import get_user
    from routes import signals
    from routes.api_v1 import approvals

    import config
    from services import action_proposals
    from services import email_credentials
    from services import privacy_mode

    monkeypatch.setattr(
        signals,
        "list_sources",
        lambda user: {
            "sources": [{"source_type": "imap"}] if w.imap.get(user.user_id) else [],
            "total": 0,
        },
    )
    monkeypatch.setattr(
        email_credentials, "sending_addresses", lambda uid: w.mailboxes.get(uid, {})
    )
    monkeypatch.setattr(
        config, "get_capability_registry", lambda: SimpleNamespace(all_services=lambda: w.services)
    )
    monkeypatch.setattr(
        approvals,
        "pending",
        lambda request, limit, as_user: SimpleNamespace(
            pending=[object()] * w.approvals.get(get_user(request).user_id, 0)
        ),
    )
    monkeypatch.setattr(
        action_proposals, "list_pending", lambda uid: [{}] * w.proposals.get(uid, 0)
    )
    monkeypatch.setattr(
        action_proposals,
        "list_recoverable_sends",
        lambda uid, **kw: [{}] * w.recoverable.get(uid, 0),
    )
    monkeypatch.setattr(
        privacy_mode,
        "effective_privacy_mode",
        lambda uid: SimpleNamespace(
            effective=InstancePrivacyMode.ALLOW_CLOUD
            if w.remote_allowed.get(uid, False)
            else InstancePrivacyMode.LOCAL_ONLY
        ),
    )
    monkeypatch.setattr(config, "get_graph_mode", lambda: w.graph)
    return w


def _get(client, monkeypatch, user="alice") -> dict:
    r = client.get(URL, headers=_hdr(monkeypatch, user))
    assert r.status_code == 200, r.text
    return r.json()


def test_all_off(client, monkeypatch, world) -> None:
    assert _get(client, monkeypatch) == {
        "email_connected": False,
        "can_act": False,
        "pending_approvals": 0,
        "voice_available": False,
        "online_ai_allowed": False,
        "knowledge_graph_on": False,
    }


def test_email_connected_follows_imap_source(client, monkeypatch, world) -> None:
    world.imap["alice"] = True
    assert _get(client, monkeypatch)["email_connected"] is True
    world.imap["alice"] = False
    assert _get(client, monkeypatch)["email_connected"] is False


def test_online_ai_allowed(client, monkeypatch, world) -> None:
    world.remote_allowed["alice"] = True
    assert _get(client, monkeypatch)["online_ai_allowed"] is True
    world.remote_allowed["alice"] = False
    assert _get(client, monkeypatch)["online_ai_allowed"] is False


@pytest.mark.parametrize(
    ("mode", "expected"), [("disabled", False), ("service", True), ("inprocess", True)]
)
def test_knowledge_graph_on(client, monkeypatch, world, mode, expected) -> None:
    world.graph = mode
    assert _get(client, monkeypatch)["knowledge_graph_on"] is expected


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("none", False), ("fake_stt", True), ("whisper_sidecar", True)],
)
def test_voice_available_follows_stt_backend(client, monkeypatch, world, backend, expected) -> None:
    monkeypatch.setenv("STT_BACKEND", backend)
    assert _get(client, monkeypatch)["voice_available"] is expected


def test_can_act_false_with_nothing(client, monkeypatch, world) -> None:
    world.services = [_svc(["memory:read"]), _svc(["x:write"], healthy=False)]
    assert _get(client, monkeypatch)["can_act"] is False


def test_can_act_from_sending_mailbox(client, monkeypatch, world) -> None:
    world.mailboxes["alice"] = {"src-1": "a@example.org"}
    assert _get(client, monkeypatch)["can_act"] is True


def test_can_act_from_healthy_write_capability(client, monkeypatch, world) -> None:
    world.services = [_svc(["calendar:write"])]
    assert _get(client, monkeypatch)["can_act"] is True


def test_can_act_ignores_unhealthy_or_read_only_capability(client, monkeypatch, world) -> None:
    world.services = [_svc(["calendar:write"], healthy=False), _svc(["calendar:read", "echo:run"])]
    assert _get(client, monkeypatch)["can_act"] is False


@pytest.mark.parametrize("kind", ["proposals", "recoverable"])
def test_can_act_from_waiting_item(client, monkeypatch, world, kind) -> None:
    getattr(world, kind)["alice"] = 1
    body = _get(client, monkeypatch)
    assert body["can_act"] is True
    assert body["pending_approvals"] == 1


def test_pending_approvals_sums_all_waiting_items(client, monkeypatch, world) -> None:
    world.proposals["alice"] = 1
    world.recoverable["alice"] = 3
    assert _get(client, monkeypatch)["pending_approvals"] == 4


def test_denied_history_is_not_waiting(client, monkeypatch, world) -> None:
    # The Approvals page's denied list is a log that never clears; it must not
    # keep the badge (or Approvals itself) up (LUM-993 Pass 2a review).
    world.approvals["alice"] = 5
    body = _get(client, monkeypatch)
    assert body["pending_approvals"] == 0
    assert body["can_act"] is False


def test_user_isolation(client, monkeypatch, world) -> None:
    world.mailboxes["bob"] = {"src-9": "b@example.org"}
    world.approvals["bob"] = 4
    world.proposals["bob"] = 1
    world.recoverable["bob"] = 2
    world.imap["bob"] = True
    world.remote_allowed["bob"] = True
    a = _get(client, monkeypatch, "alice")
    assert a["can_act"] is False
    assert a["pending_approvals"] == 0
    assert a["email_connected"] is False
    assert a["online_ai_allowed"] is False
    b = _get(client, monkeypatch, "bob")
    assert b["can_act"] is True
    assert b["pending_approvals"] == 3


def test_unauthenticated_is_401(client, monkeypatch, world) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-me-shell-401-secret")
    assert client.get(URL).status_code == 401


def test_one_failing_source_leaves_the_others(client, monkeypatch, world, caplog) -> None:
    from services import email_credentials

    world.imap["alice"] = True
    world.graph = "service"
    world.proposals["alice"] = 2

    def boom(uid):
        raise RuntimeError("credential store down")

    monkeypatch.setattr(email_credentials, "sending_addresses", boom)
    with caplog.at_level(logging.WARNING, logger="routes.api_v1.me_shell"):
        body = _get(client, monkeypatch)
    assert body["email_connected"] is True
    assert body["knowledge_graph_on"] is True
    assert body["pending_approvals"] == 2
    # (a) failed; (c) still makes can_act true.
    assert body["can_act"] is True
    assert any(
        "sending_addresses" in rec.getMessage() and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_failing_source_flag_is_false_not_500(client, monkeypatch, world, caplog) -> None:
    import config

    def boom():
        raise RuntimeError("registry gone")

    monkeypatch.setattr(config, "get_capability_registry", boom)
    with caplog.at_level(logging.WARNING, logger="routes.api_v1.me_shell"):
        body = _get(client, monkeypatch)
    assert body["can_act"] is False
    assert any("capability_registry" in rec.getMessage() for rec in caplog.records)


def test_failing_pending_source_counts_zero(client, monkeypatch, world, caplog) -> None:
    from services import action_proposals

    world.recoverable["alice"] = 2

    def boom(uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(action_proposals, "list_pending", boom)
    with caplog.at_level(logging.WARNING, logger="routes.api_v1.me_shell"):
        body = _get(client, monkeypatch)
    assert body["pending_approvals"] == 2
    assert any("pending_proposals" in rec.getMessage() for rec in caplog.records)
