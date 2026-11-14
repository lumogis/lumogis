# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/approvals/*`` — pending list, set-mode, elevate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_approvals_bucket():
    from routes.api_v1.approvals import _approval_calls

    _approval_calls.clear()
    yield
    _approval_calls.clear()


def test_pending_returns_empty_when_no_data(client):
    resp = client.get("/api/v1/approvals/pending")
    assert resp.status_code == 200
    assert resp.json() == {"pending": []}


def test_set_mode_unknown_connector_returns_404(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(reg, "list_actions", lambda: [])
    resp = client.post("/api/v1/approvals/connector/nope/mode", json={"mode": "DO"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_connector"


def test_set_mode_hard_limited_connector_403(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(
        reg,
        "list_actions",
        lambda: [{"connector": "smtp", "action_type": "send_email"}],
    )
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: True)

    resp = client.post("/api/v1/approvals/connector/smtp/mode", json={"mode": "DO"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "hard_limited_connector"


def test_set_mode_happy_path(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(
        reg,
        "list_actions",
        lambda: [{"connector": "smtp", "action_type": "draft_email"}],
    )
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)

    calls = {}

    def _set(*, user_id, connector, mode):
        calls["set"] = (user_id, connector, mode)

    monkeypatch.setattr(v1, "set_connector_mode", _set)

    written = []
    monkeypatch.setattr(v1.audit_module, "write_audit", lambda entry: written.append(entry))

    resp = client.post("/api/v1/approvals/connector/smtp/mode", json={"mode": "DO"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"connector": "smtp", "mode": "DO"}
    assert calls["set"][1] == "smtp"
    assert len(written) == 1
    assert written[0].connector == "__permissions_change__"


def test_elevate_unknown_action_returns_404(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(reg, "list_actions", lambda: [])
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)

    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "smtp", "action_type": "ghost"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_action"


def test_elevate_hard_limited_returns_403(client, monkeypatch):
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: True)

    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "shell", "action_type": "rm_rf"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "hard_limited_action"


def test_elevate_happy_path(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(
        reg,
        "list_actions",
        lambda: [{"connector": "smtp", "action_type": "draft_email"}],
    )
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)

    calls = {}

    def _elev(*, user_id, connector, action_type):
        calls["elev"] = (user_id, connector, action_type)

    monkeypatch.setattr(v1, "elevate_to_routine", _elev)

    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "smtp", "action_type": "draft_email"},
    )
    assert resp.status_code == 200
    assert resp.json()["elevated"] is True
    assert calls["elev"] == ("default", "smtp", "draft_email")


# ---------------------------------------------------------------------------
# LUM-962 — DO-mode flip agrees with the Ask/Do connector set
# ---------------------------------------------------------------------------

_DENIED_AT = "2026-11-11T10:00:00+00:00"


def _stub_actions(monkeypatch, specs: list[dict]) -> None:
    import actions.registry as reg

    monkeypatch.setattr(reg, "list_actions", lambda: specs)


def _raise_actions(monkeypatch) -> None:
    import actions.registry as reg

    def boom():
        raise RuntimeError("action registry down")

    monkeypatch.setattr(reg, "list_actions", boom)


def _raise_capabilities(monkeypatch) -> None:
    import config

    def boom():
        raise RuntimeError("capability registry down")

    monkeypatch.setattr(config, "get_capability_registry", boom)


def _capture_set_mode(monkeypatch) -> list:
    import routes.api_v1.approvals as v1

    calls: list = []
    monkeypatch.setattr(v1, "set_connector_mode", lambda **kw: calls.append(kw))
    monkeypatch.setattr(v1.audit_module, "write_audit", lambda entry: None)
    return calls


def _stub_pending(monkeypatch, denied: list[tuple[str, str]], tracking: dict | None = None):
    """Stub the two pending SQL reads. Returns the list of tracking-query calls."""
    from datetime import datetime

    import routes.api_v1.approvals as v1

    rows = [
        {
            "id": i + 1,
            "connector": c,
            "action_type": a,
            "input_summary": "x",
            "created_at": datetime.fromisoformat(_DENIED_AT),
        }
        for i, (c, a) in enumerate(denied)
    ]
    tracking_calls: list = []

    def _fetch_all(ms, sql, params):
        return rows if "FROM action_log" in sql else []

    def _fetch_one(ms, sql, params):
        assert "FROM routine_do_tracking" in sql
        tracking_calls.append(params)
        return tracking

    monkeypatch.setattr(v1, "_safe_fetch_all", _fetch_all)
    monkeypatch.setattr(v1, "_safe_fetch_one", _fetch_one)
    return tracking_calls


def _caps(monkeypatch, ids, write_tools=None):
    from tests.test_per_user_connector_permissions import _stub_capability_registry

    _stub_capability_registry(monkeypatch, ids, write_tools=write_tools)


def _by_connector(resp) -> dict:
    return {(p["connector"], p["action_type"]): p for p in resp.json()["pending"]}


# 10
def test_set_mode_known_capability_returns_200(client, monkeypatch):
    import routes.api_v1.approvals as v1

    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"], ["notes_write"])
    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)
    calls = _capture_set_mode(monkeypatch)
    written: list = []
    monkeypatch.setattr(v1.audit_module, "write_audit", lambda e: written.append(e))

    resp = client.post("/api/v1/approvals/connector/capability.notes/mode", json={"mode": "DO"})
    assert resp.status_code == 200
    assert resp.json() == {"connector": "capability.notes", "mode": "DO"}
    assert [c["connector"] for c in calls] == ["capability.notes"]
    assert len(written) == 1


# 11
def test_set_mode_capability_without_write_tools_returns_200(client, monkeypatch):
    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"])
    _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/capability.notes/mode", json={"mode": "DO"})
    assert resp.status_code == 200


# 11a
def test_set_mode_mixed_action_connector_returns_403(client, monkeypatch):
    _stub_actions(
        monkeypatch,
        [
            {"connector": "smtp", "action_type": "email_send"},
            {"connector": "smtp", "action_type": "code_commit"},
        ],
    )
    calls = _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/smtp/mode", json={"mode": "DO"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "hard_limited_connector"
    assert calls == []


# 12
def test_set_mode_capability_all_write_tools_hard_limited_returns_403(client, monkeypatch):
    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"], ["code_commit"])
    calls = _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/capability.notes/mode", json={"mode": "DO"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "hard_limited_connector"
    assert calls == []


# 12a
def test_set_mode_capability_mixed_write_tools_returns_403(client, monkeypatch):
    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"], ["notes_write", "code_commit"])
    calls = _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/capability.notes/mode", json={"mode": "DO"})
    assert resp.status_code == 403
    assert calls == []


# 13
def test_set_mode_unknown_capability_returns_404(client, monkeypatch):
    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"], ["notes_write"])
    calls = _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/capability.gone/mode", json={"mode": "DO"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"error": "unknown_connector"}
    assert calls == []


# 14
def test_set_mode_tool_only_connector_returns_404(client, monkeypatch):
    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    calls = _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/filesystem-mcp/mode", json={"mode": "DO"})
    assert resp.status_code == 404
    assert calls == []


# 15
def test_pending_capability_row_offers_switch_not_elevate(client, monkeypatch):
    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"], ["notes_write"])
    _stub_pending(monkeypatch, [("capability.notes", "notes_write")])
    row = _by_connector(client.get("/api/v1/approvals/pending"))[
        ("capability.notes", "notes_write")
    ]
    assert row["suggested_action"] == "set_connector_do"
    assert row["elevation_eligible"] is False


# 16
def test_pending_action_row_offers_switch_and_elevate(client, monkeypatch):
    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    _caps(monkeypatch, [])
    _stub_pending(monkeypatch, [("smtp", "email_send")])
    row = _by_connector(client.get("/api/v1/approvals/pending"))[("smtp", "email_send")]
    assert row["suggested_action"] == "set_connector_do"
    assert row["elevation_eligible"] is True


# 17
def test_pending_stale_connector_row_is_explain_only(client, monkeypatch):
    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    _caps(monkeypatch, ["notes"], ["notes_write"])
    _stub_pending(
        monkeypatch,
        [("filesystem-mcp", "integration_probe_read"), ("capability.gone", "x")],
    )
    rows = _by_connector(client.get("/api/v1/approvals/pending"))
    assert len(rows) == 2
    for row in rows.values():
        assert row["suggested_action"] == "explain_only"
        assert row["elevation_eligible"] is False


# 18
def test_pending_tracking_threshold_without_registered_pair_does_not_suggest_elevate(
    client, monkeypatch
):
    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"], ["notes_write"])
    _stub_pending(
        monkeypatch,
        [("capability.notes", "notes_write")],
        tracking={"approval_count": 20, "auto_approved": False, "edit_count": 0},
    )
    row = _by_connector(client.get("/api/v1/approvals/pending"))[
        ("capability.notes", "notes_write")
    ]
    assert row["suggested_action"] == "set_connector_do"


# 19
def test_pending_elevation_candidate_unregistered_pair_not_eligible(client, monkeypatch):
    import routes.api_v1.approvals as v1

    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    _caps(monkeypatch, [])

    def _fetch_all(ms, sql, params):
        if "FROM action_log" in sql:
            return []
        return [
            {"connector": "smtp", "action_type": "email_send", "approval_count": 16},
            {"connector": "capability.notes", "action_type": "notes_write", "approval_count": 16},
        ]

    monkeypatch.setattr(v1, "_safe_fetch_all", _fetch_all)
    rows = _by_connector(client.get("/api/v1/approvals/pending"))
    assert rows[("smtp", "email_send")]["elevation_eligible"] is True
    assert rows[("capability.notes", "notes_write")]["elevation_eligible"] is False


# 19a
def test_pending_mixed_capability_row_is_explain_only(client, monkeypatch):
    _stub_actions(monkeypatch, [])
    _caps(monkeypatch, ["notes"], ["notes_write", "code_commit"])
    _stub_pending(monkeypatch, [("capability.notes", "notes_write")])
    row = _by_connector(client.get("/api/v1/approvals/pending"))[
        ("capability.notes", "notes_write")
    ]
    assert row["suggested_action"] == "explain_only"
    assert row["elevation_eligible"] is False


# 19b
def test_pending_unsettable_row_skips_tracking_query(client, monkeypatch):
    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    _caps(monkeypatch, [])
    tracking_calls = _stub_pending(monkeypatch, [("filesystem-mcp", "integration_probe_read")])
    resp = client.get("/api/v1/approvals/pending")
    assert resp.status_code == 200
    assert tracking_calls == []


# 19c
def test_pending_and_set_mode_agree(client, monkeypatch):
    """A row marked ``set_connector_do`` is one ``set_mode`` accepts, and vice versa."""
    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    from tests.test_per_user_connector_permissions import _stub_capability_registry

    # `notes` is clean, `mixed` carries a hard-limited write tool.
    _stub_capability_registry(monkeypatch, ["notes"], write_tools=["notes_write"])
    import config

    clean_reg = config.get_capability_registry()
    _stub_capability_registry(monkeypatch, ["mixed"], write_tools=["mixed_write", "code_commit"])
    mixed_reg = config.get_capability_registry()

    class _Both:
        def all_services(self):
            return clean_reg.all_services() + mixed_reg.all_services()

        def get_service(self, sid):
            return clean_reg.get_service(sid) or mixed_reg.get_service(sid)

    monkeypatch.setattr(config, "get_capability_registry", lambda: _Both())
    _capture_set_mode(monkeypatch)
    denied = [
        ("capability.notes", "notes_write"),
        ("capability.mixed", "mixed_write"),
        ("smtp", "email_send"),
        ("filesystem-mcp", "integration_probe_read"),
    ]
    _stub_pending(monkeypatch, denied)
    rows = _by_connector(client.get("/api/v1/approvals/pending"))
    outcomes = {}
    for (connector, action_type), row in rows.items():
        post = client.post(f"/api/v1/approvals/connector/{connector}/mode", json={"mode": "DO"})
        assert (row["suggested_action"] == "set_connector_do") == (post.status_code == 200), (
            connector,
            row["suggested_action"],
            post.status_code,
        )
        outcomes[connector] = post.status_code
    assert outcomes == {
        "capability.notes": 200,
        "capability.mixed": 403,
        "smtp": 200,
        "filesystem-mcp": 404,
    }


# 20
def test_set_mode_action_registry_down_returns_404(client, monkeypatch):
    _raise_actions(monkeypatch)
    calls = _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/smtp/mode", json={"mode": "DO"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_connector"
    assert calls == []


# 21 / 22b
def test_set_mode_capability_registry_down_returns_404_and_never_writes(client, monkeypatch):
    _raise_capabilities(monkeypatch)
    calls = _capture_set_mode(monkeypatch)
    resp = client.post("/api/v1/approvals/connector/capability.notes/mode", json={"mode": "DO"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_connector"
    assert calls == []


# 22
def test_pending_registry_down_marks_rows_explain_only(client, monkeypatch):
    _raise_actions(monkeypatch)
    _caps(monkeypatch, [])
    _stub_pending(monkeypatch, [("smtp", "email_send")])
    resp = client.get("/api/v1/approvals/pending")
    assert resp.status_code == 200
    row = _by_connector(resp)[("smtp", "email_send")]
    assert row["suggested_action"] == "explain_only"
    assert row["elevation_eligible"] is False


# 22a
def test_pending_capability_registry_down_marks_capability_rows_explain_only(client, monkeypatch):
    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    _raise_capabilities(monkeypatch)
    _stub_pending(monkeypatch, [("capability.notes", "notes_write"), ("smtp", "email_send")])
    resp = client.get("/api/v1/approvals/pending")
    assert resp.status_code == 200
    rows = _by_connector(resp)
    cap = rows[("capability.notes", "notes_write")]
    assert cap["suggested_action"] == "explain_only"
    assert cap["elevation_eligible"] is False
    assert rows[("smtp", "email_send")]["suggested_action"] == "set_connector_do"


# 22c
def test_set_mode_outage_warn_escapes_connector(client, monkeypatch, caplog):
    import logging

    _raise_actions(monkeypatch)
    _capture_set_mode(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="routes.api_v1.approvals"):
        resp = client.post("/api/v1/approvals/connector/evil%0Aforged/mode", json={"mode": "DO"})
    assert resp.status_code == 404
    msgs = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("approvals_set_mode_registry_unavailable")
    ]
    assert len(msgs) == 1
    assert "\n" not in msgs[0]
    assert "'evil\\nforged'" in msgs[0]


# 23
def test_elevate_capability_still_404(client, monkeypatch):
    import routes.api_v1.approvals as v1

    _stub_actions(monkeypatch, [{"connector": "smtp", "action_type": "email_send"}])
    _caps(monkeypatch, ["notes"], ["notes_write"])
    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)
    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "capability.notes", "action_type": "notes_write"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_action"


# 23a
def test_elevate_registry_down_returns_404(client, monkeypatch):
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)
    _raise_actions(monkeypatch)
    elevated: list = []
    monkeypatch.setattr(v1, "elevate_to_routine", lambda **kw: elevated.append(kw))
    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "smtp", "action_type": "email_send"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_action"
    assert elevated == []
