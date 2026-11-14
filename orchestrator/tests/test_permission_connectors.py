# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-962 — ``services.permission_connectors``: which connectors are Ask/Do-settable.

Includes the ADR 220 parity test: ``PUT /me/permissions`` and Approvals'
``set_mode`` must agree on the connector set.

§5a — registry outages are simulated by making the registry **raise**, which is
what the helpers actually catch, not by patching the helpers to return ``None``.
"""

from __future__ import annotations

import logging

from services.permission_connectors import ConnectorGate
from tests.test_per_user_connector_permissions import _booted_client
from tests.test_per_user_connector_permissions import _create_user
from tests.test_per_user_connector_permissions import _hdr
from tests.test_per_user_connector_permissions import _stub_capability_registry
from tests.test_per_user_connector_permissions import _stub_registry
from tests.test_per_user_connector_permissions import auth_env  # noqa: F401 — fixture
from tests.test_per_user_connector_permissions import perm_store  # noqa: F401 — fixture

from services import permission_connectors as pc


def _raise_registry(monkeypatch) -> None:
    import actions.registry as reg

    def boom():
        raise RuntimeError("action registry down")

    monkeypatch.setattr(reg, "list_actions", boom)


def _raise_capability_registry(monkeypatch) -> None:
    import config

    def boom():
        raise RuntimeError("capability registry down")

    monkeypatch.setattr(config, "get_capability_registry", boom)


# 1
def test_connector_gate_action_connector(monkeypatch):
    _stub_registry(monkeypatch, ["smtp"])
    assert pc.connector_gate("smtp") == ConnectorGate(True, ("noop",))


# 2
def test_connector_gate_tool_only_connector_not_settable(monkeypatch):
    _stub_registry(monkeypatch, ["smtp", "imap"])
    assert pc.connector_gate("filesystem-mcp") == ConnectorGate(False, ())


# 3
def test_connector_gate_known_and_unknown_capability(monkeypatch):
    _stub_capability_registry(monkeypatch, ["notes"], write_tools=["notes_write"])
    assert pc.connector_gate("capability.notes") == ConnectorGate(True, ("notes_write",))
    # default stub: one read tool only
    _stub_capability_registry(monkeypatch, ["notes"])
    assert pc.connector_gate("capability.notes") == ConnectorGate(True, ())
    assert pc.connector_gate("capability.gone") == ConnectorGate(False, ())


# 4
def test_connector_gate_outage_is_none_per_kind(monkeypatch, caplog):
    _stub_registry(monkeypatch, ["smtp"])
    _stub_capability_registry(monkeypatch, ["notes"], write_tools=["notes_write"])

    _raise_registry(monkeypatch)
    assert pc.connector_gate("smtp") is None
    # an action-registry outage never affects the capability kind
    assert pc.connector_gate("capability.notes") == ConnectorGate(True, ("notes_write",))

    monkeypatch.undo()
    _stub_registry(monkeypatch, ["smtp"])
    _raise_capability_registry(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="services.permission_connectors"):
        assert pc.connector_gate("capability.notes") is None
    assert any(
        r.name == "services.permission_connectors"
        and r.getMessage() == "capability_registry_unavailable_at_permission_validation"
        for r in caplog.records
    )
    assert pc.connector_gate("smtp") == ConnectorGate(True, ("noop",))


# 4a
def test_connector_gate_settable_matches_known_sets(monkeypatch):
    """The snapshot path and the set path (used by ``PUT /me/permissions``) agree."""
    _stub_registry(monkeypatch, ["smtp", "imap"])
    _stub_capability_registry(monkeypatch, ["notes"], write_tools=["notes_write"])
    known = pc.known_action_connectors() | pc.known_capability_connectors()
    for c in ("smtp", "imap", "filesystem-mcp", "capability.notes", "capability.gone"):
        assert pc.connector_gate(c).settable == (c in known), c


# 4b
def test_connector_gate_reads_capability_registry_once(monkeypatch):
    """Keyed lookup, no scan, single snapshot."""
    import config

    _stub_capability_registry(monkeypatch, ["notes"], write_tools=["notes_write"])
    inner = config.get_capability_registry()
    calls: list[str] = []

    class _Reg:
        def all_services(self):
            raise AssertionError("connector_gate must not scan all_services()")

        def get_service(self, service_id):
            calls.append(service_id)
            return inner.get_service(service_id)

    monkeypatch.setattr(config, "get_capability_registry", lambda: _Reg())
    assert pc.connector_gate("capability.notes") == ConnectorGate(True, ("notes_write",))
    assert calls == ["notes"]


# 5
def test_known_action_connectors_none_when_registry_raises(monkeypatch, caplog):
    _raise_registry(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="services.permission_connectors"):
        assert pc.known_action_connectors() is None
    assert any(
        r.name == "services.permission_connectors"
        and r.getMessage() == "action_registry_unavailable_at_permission_validation"
        for r in caplog.records
    )


# 6
def test_registered_action_pairs(monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(
        reg,
        "list_actions",
        lambda: [
            {"connector": "smtp", "action_type": "email_send"},
            {"connector": "imap", "action_type": "email_tidy"},
            {"connector": "", "action_type": "orphan"},
        ],
    )
    assert pc.registered_action_pairs() == {("smtp", "email_send"), ("imap", "email_tidy")}
    _raise_registry(monkeypatch)
    assert pc.registered_action_pairs() is None


# 7
def test_do_refused_any_not_all():
    from actions.executor import is_hard_limited

    assert pc.do_refused((), is_hard_limited=is_hard_limited) is False
    assert pc.do_refused(("notes_write",), is_hard_limited=is_hard_limited) is False
    assert pc.do_refused(("notes_write", "code_commit"), is_hard_limited=is_hard_limited) is True
    assert pc.do_refused(("code_commit",), is_hard_limited=is_hard_limited) is True


# 7a
def test_capability_write_types_match_tool_catalog(monkeypatch):
    """Pins ``action_type == tool.name`` against the catalog builder's derivation."""
    import config
    from services import unified_tools

    _stub_capability_registry(monkeypatch, ["notes"], write_tools=["notes_write", "notes_delete"])
    svc = config.get_capability_registry().get_service("notes")
    entries = unified_tools._entries_for_capability_service(svc)
    from_catalog = {(e.connector, e.action_type) for e in entries if e.is_write}
    gate = pc.connector_gate("capability.notes")
    assert from_catalog == {("capability.notes", t) for t in gate.gated_action_types}
    assert gate.gated_action_types == ("notes_delete", "notes_write")


# 9 — ADR 220 parity
def test_put_permissions_and_approvals_set_mode_agree_on_connector_set(
    perm_store,  # noqa: F811
    auth_env,  # noqa: F811
    monkeypatch,
):
    """``PUT /me/permissions`` and ``POST /approvals/connector/{c}/mode`` accept the same set.

    Scope of the claim: **connector validity only**. The hard-limit 403 is an
    Approvals-only refusal (``PUT`` checks no hard limits), so no hard-limited
    connector is in this set. Status codes are compared, not 404 bodies, which
    differ on purpose (``PUT`` adds a ``connector`` key).
    """
    import routes.api_v1.approvals as v1

    _stub_registry(monkeypatch, ["smtp"])
    _stub_capability_registry(monkeypatch, ["notes"], write_tools=["notes_write"])
    monkeypatch.setattr(v1.audit_module, "write_audit", lambda entry: None)
    v1._approval_calls.clear()

    alice = _create_user(perm_store, user_id="alice", email="a@home.lan", role="user")
    expected = {
        "smtp": 200,
        "capability.notes": 200,
        "capability.gone": 404,
        "filesystem-mcp": 404,
    }
    with _booted_client() as client:
        for connector, want in expected.items():
            put = client.put(
                f"/api/v1/me/permissions/{connector}",
                headers=_hdr(alice),
                json={"mode": "DO"},
            )
            post = client.post(
                f"/api/v1/approvals/connector/{connector}/mode",
                headers=_hdr(alice),
                json={"mode": "DO"},
            )
            assert (put.status_code, post.status_code) == (want, want), (
                connector,
                put.text,
                post.text,
            )
    v1._approval_calls.clear()
