# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``POST /api/v1/admin/knowledge-graph/{enable,disable}`` (LUM-989)."""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone

import pytest
from csrf import require_same_origin
from fastapi.testclient import TestClient
from tests.test_graph_toggle import GIB
from tests.test_graph_toggle import SECRET_SENTINEL
from tests.test_graph_toggle import Install

from services import graph_toggle

BASE = "/api/v1/admin/knowledge-graph"


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def inst(tmp_path, monkeypatch) -> Install:
    return Install(tmp_path, monkeypatch)


@pytest.fixture
def run_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(graph_toggle, "run", lambda op, **_k: calls.append(op))
    return calls


def _hdr(monkeypatch: pytest.MonkeyPatch, role: str = "admin") -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-knowledge-graph-toggle-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    user = "admin-1" if role == "admin" else "bob"
    return {"Authorization": f"Bearer {mint_access_token(user, role)}"}


@pytest.mark.parametrize("path", ["/enable", "/disable"])
def test_anonymous_gets_401(client, monkeypatch, path) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-knowledge-graph-toggle-401")
    assert client.post(BASE + path, json={}).status_code == 401


@pytest.mark.parametrize("path", ["/enable", "/disable"])
def test_member_gets_403(client, monkeypatch, path) -> None:
    assert client.post(BASE + path, json={}, headers=_hdr(monkeypatch, "user")).status_code == 403


@pytest.mark.parametrize("path", ["/enable", "/disable"])
def test_both_write_routes_depend_on_require_same_origin(client, path) -> None:
    """Admin calls use a Bearer token, which the Origin check skips by design, so
    the dependency is checked by introspection rather than by a cross-origin call.

    The route is read from the module's own router, where the dependency is
    declared. Newer FastAPI keeps an included router as one ``_IncludedRouter`` entry
    in ``app.routes`` instead of flattening its routes, so a lookup there finds
    nothing (StopIteration, LUM-1003). That the app serves these paths is shown by
    the 401/403 tests above."""
    from routes.api_v1 import admin_knowledge_graph

    route = next(r for r in admin_knowledge_graph.router.routes if r.path == BASE + path)
    deps = {d.call for d in route.dependant.dependencies}
    assert require_same_origin in deps


def test_enable_202_runs_the_start_in_the_background(client, monkeypatch, inst, run_calls) -> None:
    r = client.post(BASE + "/enable", json={}, headers=_hdr(monkeypatch))
    assert r.status_code == 202 and r.json() == {"state": "starting"}
    assert run_calls == ["start"]
    assert "GRAPH_MODE=service" in inst.env_text()


def test_disable_202_runs_the_stop_in_the_background(client, monkeypatch, inst, run_calls) -> None:
    inst.env.write_text("GRAPH_MODE=service\n")
    inst.modes(env="service", effective="service")
    r = client.post(BASE + "/disable", headers=_hdr(monkeypatch))
    assert r.status_code == 202 and r.json() == {"state": "stopping"}
    assert run_calls == ["stop"]


def test_enable_low_memory_needs_confirmation_then_succeeds(
    client, monkeypatch, inst, run_calls
) -> None:
    inst.memory(4 * GIB)
    hdr = _hdr(monkeypatch)
    r = client.post(BASE + "/enable", json={}, headers=hdr)
    assert r.status_code == 409 and r.json() == {"detail": "needs_confirmation"}
    assert run_calls == []
    r = client.post(BASE + "/enable", json={"confirm_low_memory": True}, headers=hdr)
    assert r.status_code == 202 and run_calls == ["start"]


def test_enable_on_a_checkout_is_blocked(client, monkeypatch, inst, run_calls) -> None:
    inst.compose.write_text("services: {}\n")
    r = client.post(BASE + "/enable", json={}, headers=_hdr(monkeypatch))
    assert r.status_code == 409 and r.json() == {"detail": "blocked", "block": "checkout"}
    assert run_calls == []


def test_enable_with_an_old_release_compose_is_blocked(
    client, monkeypatch, inst, run_calls
) -> None:
    inst.compose.write_text(
        "# lumogis-compose: 0123456789abcdef\nservices:\n  core:\n    image: x\n"
    )
    r = client.post(BASE + "/enable", json={}, headers=_hdr(monkeypatch))
    assert r.json() == {"detail": "blocked", "block": "compose_too_old"}


def test_a_second_enable_while_starting_is_409_busy(client, monkeypatch, inst, run_calls) -> None:
    hdr = _hdr(monkeypatch)
    assert client.post(BASE + "/enable", json={}, headers=hdr).status_code == 202
    r = client.post(BASE + "/enable", json={}, headers=hdr)
    assert r.status_code == 409 and r.json() == {"detail": "busy"}
    assert run_calls == ["start"]  # one sequence only


def test_disable_while_starting_is_409_busy(client, monkeypatch, inst, run_calls) -> None:
    hdr = _hdr(monkeypatch)
    client.post(BASE + "/enable", json={}, headers=hdr)
    r = client.post(BASE + "/disable", headers=hdr)
    assert r.status_code == 409 and r.json() == {"detail": "busy"}


@pytest.mark.parametrize(
    "body", [{"confirm_low_memory": "yes"}, {"x": 1}, {"confirm_low_memory": 5}]
)
def test_enable_rejects_a_bad_body_with_422(client, monkeypatch, inst, run_calls, body) -> None:
    r = client.post(BASE + "/enable", json=body, headers=_hdr(monkeypatch))
    assert r.status_code == 422
    assert run_calls == [] and "GRAPH_MODE=service" not in inst.env_text()


def test_disable_when_off_is_blocked_already_off(client, monkeypatch, inst, run_calls) -> None:
    r = client.post(BASE + "/disable", headers=_hdr(monkeypatch))
    assert r.status_code == 409 and r.json() == {"detail": "blocked", "block": "already_off"}


def test_the_secret_appears_in_no_body_log_or_audit_row(
    client, monkeypatch, inst, run_calls, caplog
) -> None:
    inst.env.write_text(f"GRAPH_WEBHOOK_SECRET={SECRET_SENTINEL}\n")
    hdr = _hdr(monkeypatch)
    bodies = []
    with caplog.at_level(logging.DEBUG):
        r = client.post(BASE + "/enable", json={}, headers=hdr)
        bodies.append(r.text)
        bodies.append(client.post(BASE + "/enable", json={}, headers=hdr).text)  # 409 busy
        bodies.append(client.get(BASE, headers=hdr).text)
    inst.modes(env="service", effective="service")
    graph_toggle._save(
        {
            graph_toggle.KEY_FAILED_AT: datetime.now(timezone.utc).isoformat(),
            graph_toggle.KEY_FAILED_CODE: "compose_failed",
        }
    )
    with caplog.at_level(logging.DEBUG):
        bodies.append(client.post(BASE + "/disable", headers=hdr).text)
        bodies.append(client.get(BASE, headers=hdr).text)
    assert all(SECRET_SENTINEL not in b for b in bodies)
    assert SECRET_SENTINEL not in caplog.text
    for entry in inst.audits:
        assert SECRET_SENTINEL not in (entry.input_summary + entry.result_summary)
    assert len(inst.audits) >= 1


def test_the_status_response_reports_a_release_install_with_buttons(
    client, monkeypatch, inst
) -> None:
    body = client.get(BASE, headers=_hdr(monkeypatch)).json()
    assert body["install_kind"] == "release"
    assert body["turn_on"] == {"available": True, "block": None, "needs_confirmation": False}
    assert body["turn_off"] == {"available": False, "block": "already_off"}
    assert body["failed_code"] is None


def test_the_status_after_enable_reads_starting(client, monkeypatch, inst, run_calls) -> None:
    hdr = _hdr(monkeypatch)
    client.post(BASE + "/enable", json={}, headers=hdr)
    inst.modes(env="disabled", effective="disabled")  # Core not yet recreated
    body = client.get(BASE, headers=hdr).json()
    assert body["state"] == "starting"
    assert body["turn_on"]["block"] == "in_progress"
