# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-962 — the RC approvals seed must be a state the product can reach.

The seed used to log a denial for ``filesystem-mcp`` / ``integration_probe_read``.
Only ``permissions.check_permission`` writes ``action_log.allowed = FALSE``, and
only for a *write*; the in-process tool connectors are all reads, so that row
could never exist in a running Core and Approvals (correctly) refused to flip it.

These tests prove the seed's target is derived from the live action registry and
that an impossible target is refused *before* the previous seed row is deleted.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
import scripts.seed_public_rc_approvals_fixture as seed_mod


def test_seed_target_is_a_real_non_hard_limited_write():
    from actions.executor import is_hard_limited
    from actions.registry import get_action

    assert seed_mod._seed_target() == ("smtp", "email_send")
    spec = get_action("email_reply")
    assert spec is not None and spec.is_write is True
    assert is_hard_limited("email_send") is False


def test_seed_target_is_reachable_denial(monkeypatch):
    """Pin: ``check_permission`` under ASK denies and logs exactly the seeded pair.

    The reachability proof proper is the test above (a registered, non-hard-limited
    write); this pins the behaviour it relies on.
    """
    import permissions

    connector, action_type = seed_mod._seed_target()
    logged: list[dict] = []
    monkeypatch.setattr(permissions, "get_connector_mode", lambda *, user_id, connector: "ASK")
    monkeypatch.setattr(permissions, "log_action", lambda **kw: logged.append(kw))

    assert permissions.check_permission(connector, action_type, True, user_id="u") is False
    assert len(logged) == 1
    assert (logged[0]["connector"], logged[0]["action_type"]) == (connector, action_type)
    assert logged[0]["allowed"] is False


def test_seed_target_refuses_hard_limited(monkeypatch):
    from actions.registry import get_action

    real = get_action("email_reply")
    assert real is not None

    # hard-limited action_type
    monkeypatch.setattr(
        seed_mod, "get_action", lambda n: dataclasses.replace(real, action_type="code_commit")
    )
    with pytest.raises(RuntimeError, match="hard-limited"):
        seed_mod._seed_target()

    # not a write: ASK never denies it
    monkeypatch.setattr(seed_mod, "get_action", lambda n: dataclasses.replace(real, is_write=False))
    with pytest.raises(RuntimeError, match="not a write"):
        seed_mod._seed_target()

    # not registered
    monkeypatch.setattr(seed_mod, "get_action", lambda n: None)
    with pytest.raises(RuntimeError, match="not registered"):
        seed_mod._seed_target()


def test_seed_main_refused_target_keeps_previous_row(monkeypatch):
    executed: list[str] = []

    class _Store:
        def execute(self, sql, params=None):
            executed.append(sql)

    def _refuse():
        raise RuntimeError("impossible target")

    monkeypatch.setattr(seed_mod.auth_mod, "auth_enabled", lambda: True)
    monkeypatch.setenv("LUMOGIS_WEB_SMOKE_EMAIL", "smoke@home.lan")
    monkeypatch.setattr(
        seed_mod.users_svc, "get_user_by_email", lambda email: SimpleNamespace(id="u1")
    )
    monkeypatch.setattr(seed_mod.config, "get_metadata_store", lambda: _Store())
    monkeypatch.setattr(seed_mod, "_seed_target", _refuse)

    assert seed_mod.main() == 1
    assert not [s for s in executed if "DELETE" in s.upper()]
