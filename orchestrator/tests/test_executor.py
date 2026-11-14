# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for actions/executor.py.

Tests cover: unknown action, ASK-mode block, DO-mode execution,
hard-limit enforcement, audit log write, and reverse_token assignment.
No Docker or network required.
"""

from actions.executor import execute
from actions.executor import is_hard_limited
from models.actions import ActionResult
from models.actions import ActionSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    name="test_action",
    connector="filesystem",
    action_type="read_file",
    is_write=False,
    is_reversible=False,
    handler=None,
    reverse_action_name=None,
) -> ActionSpec:
    if handler is None:

        def handler(inp):
            return ActionResult(success=True, output="ok")

    return ActionSpec(
        name=name,
        connector=connector,
        action_type=action_type,
        is_write=is_write,
        is_reversible=is_reversible,
        handler=handler,
        reverse_action_name=reverse_action_name,
    )


# ---------------------------------------------------------------------------
# is_hard_limited
# ---------------------------------------------------------------------------


class TestIsHardLimited:
    def test_hard_limited_types(self):
        for t in [
            "financial_transaction",
            "mass_communication",
            "permanent_deletion",
            "first_contact",
            "code_commit",
        ]:
            assert is_hard_limited(t) is True

    def test_non_limited_types(self):
        for t in ["read_file", "write_file", "send_message", "create_event"]:
            assert is_hard_limited(t) is False


# ---------------------------------------------------------------------------
# execute — unknown action
# ---------------------------------------------------------------------------


class TestExecuteUnknownAction:
    def test_returns_error_for_unknown_action(self, monkeypatch):
        monkeypatch.setattr("actions.executor.get_action", lambda name: None)

        result = execute("nonexistent_action", user_id="test-user")

        assert result.success is False
        assert "Unknown action" in result.error


# ---------------------------------------------------------------------------
# execute — permission denied (ASK mode blocks write)
# ---------------------------------------------------------------------------


class TestExecutePermissionDenied:
    def test_write_blocked_in_ask_mode(self, monkeypatch):
        spec = _make_spec(is_write=True, action_type="write_file")
        monkeypatch.setattr("actions.executor.get_action", lambda name: spec)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **kw: False)
        monkeypatch.setattr("actions.executor.write_audit", lambda entry, **kw: "audit-id")
        monkeypatch.setattr("hooks.fire_background", lambda *a, **kw: None)

        result = execute("test_write_action", user_id="default")

        assert result.success is False
        assert "Permission denied" in result.error

    def test_audit_written_on_denial(self, monkeypatch):
        spec = _make_spec(is_write=True)
        monkeypatch.setattr("actions.executor.get_action", lambda name: spec)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **kw: False)
        monkeypatch.setattr("hooks.fire_background", lambda *a, **kw: None)

        audit_calls = []
        monkeypatch.setattr(
            "actions.executor.write_audit",
            lambda entry, **kw: audit_calls.append(entry) or "id",
        )

        execute("test_write_action", user_id="test-user")
        assert len(audit_calls) == 1


# ---------------------------------------------------------------------------
# execute — successful execution
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    def _run(self, monkeypatch, spec):
        monkeypatch.setattr("actions.executor.get_action", lambda name: spec)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **kw: True)
        monkeypatch.setattr("permissions.routine_check", lambda *a, **kw: None)
        monkeypatch.setattr("actions.executor.write_audit", lambda entry, **kw: "audit-1")
        monkeypatch.setattr("hooks.fire_background", lambda *a, **kw: None)
        return execute(spec.name, user_id="test-user")

    def test_handler_result_returned(self, monkeypatch):
        spec = _make_spec(handler=lambda inp: ActionResult(success=True, output="file contents"))
        result = self._run(monkeypatch, spec)
        assert result.success is True
        assert result.output == "file contents"

    def test_reverse_token_assigned_for_reversible(self, monkeypatch):
        spec = _make_spec(is_write=True, is_reversible=True, reverse_action_name="undo_write")
        result = self._run(monkeypatch, spec)
        assert result.reverse_token is not None

    def test_no_reverse_token_for_non_reversible(self, monkeypatch):
        spec = _make_spec(is_write=True, is_reversible=False)
        result = self._run(monkeypatch, spec)
        assert result.reverse_token is None

    def test_handler_exception_returns_error_result(self, monkeypatch):
        def bad_handler(inp):
            raise RuntimeError("disk full")

        spec = _make_spec(handler=bad_handler)
        result = self._run(monkeypatch, spec)
        assert result.success is False
        assert "disk full" in result.error

    def test_audit_written_on_success(self, monkeypatch):
        spec = _make_spec()
        audit_calls = []
        monkeypatch.setattr("actions.executor.get_action", lambda name: spec)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **kw: True)
        monkeypatch.setattr("permissions.routine_check", lambda *a, **kw: None)
        monkeypatch.setattr(
            "actions.executor.write_audit",
            lambda entry, **kw: audit_calls.append(entry) or "id",
        )
        monkeypatch.setattr("hooks.fire_background", lambda *a, **kw: None)

        execute(spec.name, user_id="test-user")
        assert len(audit_calls) == 1
        assert audit_calls[0].action_name == "test_action"

    def test_hard_limited_action_skips_routine_check(self, monkeypatch):
        spec = _make_spec(is_write=True, action_type="financial_transaction")
        routine_calls = []
        monkeypatch.setattr("actions.executor.get_action", lambda name: spec)
        monkeypatch.setattr("permissions.check_permission", lambda *a, **kw: True)
        monkeypatch.setattr(
            "permissions.routine_check",
            lambda *a, **kw: routine_calls.append(True),
        )
        monkeypatch.setattr("actions.executor.write_audit", lambda entry, **kw: "id")
        monkeypatch.setattr("hooks.fire_background", lambda *a, **kw: None)

        execute(spec.name, user_id="test-user")
        assert routine_calls == []


# ---------------------------------------------------------------------------
# LUM-794 — act-tier handlers register independently of BATCH_QUEUE_ENABLED.
#
# The two handler imports used to live inside main.py's
# `if scheduler and _batch_queue_enabled:` block, so turning off the batch queue
# (or starting without a scheduler) left the action registry EMPTY and every
# approved proposal failed with `Unknown action`. That was survivable while a
# non-permanent failure returned the row to `approved`; since LUM-762 made failure
# terminal it destroys the proposal outright, and until LUM-793 ships a `dead` row
# is visible on no surface.
# ---------------------------------------------------------------------------


def test_act_tier_handlers_register_on_import_alone() -> None:
    """No env var, no scheduler, no queue — importing the module is the contract.

    **This test does NOT carry the regression weight for LUM-794.** It asserts a
    property that already held before the fix and would pass identically with the
    imports still inside the gate, because it imports the handler modules directly
    and never touches `main.py`. Confirmed by mutation: reverting the placement
    fails only `test_main_registers_handlers_outside_the_batch_queue_gate`.

    It earns its place by pinning the precondition the fix relies on — that
    registration needs nothing but an import, so hoisting it out of the gate is
    safe — and by exercising the `_REGISTERED` idempotency guard via the second
    `register_all()` call below. The structural test is the regression guard.
    """
    from actions import registry as _registry
    from actions.handlers import imap_writeback
    from actions.handlers import smtp_send

    smtp_send.register_all()
    imap_writeback.register_all()

    names = set(_registry._registry)
    assert any(n.startswith("email_") for n in names), (
        f"no email_* action registered from module import; registry={sorted(names)[:10]}"
    )


def test_main_registers_handlers_outside_the_batch_queue_gate() -> None:
    """Structural, and deliberately so.

    The defect was pure placement: correct code in the wrong block. There is no
    lifespan harness in this tree for ANY queue, so the behavioural route would mean
    building one for a two-line import move. This reads main.py's source and asserts
    the handler imports occur BEFORE the `_batch_queue_enabled` conditional — which
    is exactly the property that regressed, and it fails loudly if someone tucks them
    back inside.
    """
    import inspect

    import main as main_mod

    src = inspect.getsource(main_mod)
    # Anchor on the STATEMENT, not the bare string: the explanatory comment above the
    # imports quotes the gate verbatim, and matching that instead put the "gate" before
    # the imports and failed the test against correct code. Found by running it.
    gate = src.index("\n    if scheduler and _batch_queue_enabled:")
    for handler in (
        "\n    from actions.handlers import smtp_send",
        "\n    from actions.handlers import imap_writeback",
    ):
        at = src.index(handler)
        assert at < gate, f"{handler.strip()!r} must not sit inside the BATCH_QUEUE_ENABLED gate"
