# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3 contract tests.

Every function we migrated to "user_id keyword-only and required" must
fail loud (TypeError) when called the old way. These tests don't
exercise behaviour — they pin the contract so a future refactor can't
silently re-add an opportunistic default.
"""

from __future__ import annotations

import pytest


def test_loop_ask_requires_user_id_kwarg() -> None:
    from loop import ask

    with pytest.raises(TypeError):
        ask("hello")


def test_loop_ask_stream_requires_user_id_kwarg() -> None:
    from loop import ask_stream

    with pytest.raises(TypeError):
        list(ask_stream("hello"))


def test_run_tool_requires_user_id_kwarg() -> None:
    from services.tools import run_tool

    with pytest.raises(TypeError):
        run_tool("noop", {})


def test_check_permission_requires_user_id_kwarg() -> None:
    from permissions import check_permission

    with pytest.raises(TypeError):
        check_permission("filesystem-mcp", "read", False)  # type: ignore[call-arg]


def test_log_action_requires_user_id_kwarg() -> None:
    from permissions import log_action

    with pytest.raises(TypeError):
        log_action("filesystem-mcp", "read", "ASK", True)  # type: ignore[call-arg]


def test_routine_check_requires_user_id_kwarg() -> None:
    from permissions import routine_check

    with pytest.raises(TypeError):
        routine_check("filesystem-mcp", "read")  # type: ignore[call-arg]


def test_get_connector_mode_requires_user_id_kwarg() -> None:
    from permissions import get_connector_mode

    with pytest.raises(TypeError):
        get_connector_mode("filesystem-mcp")  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        get_connector_mode(connector="filesystem-mcp")  # type: ignore[call-arg]


def test_executor_execute_requires_user_id_kwarg() -> None:
    from actions.executor import execute

    with pytest.raises(TypeError):
        execute("noop")


def test_attempt_reverse_requires_user_id_kwarg() -> None:
    from actions.reversibility import attempt_reverse

    with pytest.raises(TypeError):
        attempt_reverse("any-token")  # type: ignore[call-arg]


def test_get_audit_requires_user_id_kwarg() -> None:
    from actions.audit import get_audit

    with pytest.raises(TypeError):
        get_audit()


def test_ingest_file_requires_user_id_kwarg() -> None:
    from services.ingest import ingest_file

    with pytest.raises(TypeError):
        ingest_file("/tmp/does-not-matter.txt")


def test_ingest_folder_requires_user_id_kwarg() -> None:
    from services.ingest import ingest_folder

    with pytest.raises(TypeError):
        ingest_folder("/tmp")


def test_record_explicit_requires_user_id_kwarg() -> None:
    from services.feedback import record_explicit

    with pytest.raises(TypeError):
        record_explicit("signal", "abc", True)  # type: ignore[call-arg]


def test_record_implicit_requires_user_id_kwarg() -> None:
    from services.feedback import record_implicit

    with pytest.raises(TypeError):
        record_implicit("signal", "abc", "opened")  # type: ignore[call-arg]


def test_run_routine_requires_user_id_kwarg() -> None:
    from services.routines import run_routine

    with pytest.raises(TypeError):
        run_routine("weekly_review")


def test_routine_cron_callback_passes_user_id_kwarg() -> None:
    from unittest.mock import patch

    from services.routines import _job_callback

    with patch("services.routines.run_routine") as mock_run:
        _job_callback("weekly_review", "alice")
    mock_run.assert_called_once_with("weekly_review", user_id="alice")


def test_list_routines_requires_user_id_kwarg() -> None:
    from services.routines import list_routines

    with pytest.raises(TypeError):
        list_routines()


def test_mcp_resolve_user_id_raises_without_jwt_or_env(monkeypatch) -> None:
    from mcp_server import _resolve_user_id

    monkeypatch.delenv("MCP_DEFAULT_USER_ID", raising=False)
    with pytest.raises(RuntimeError):
        _resolve_user_id()


def test_mcp_resolve_user_id_uses_env_fallback(monkeypatch) -> None:
    from mcp_server import _resolve_user_id

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "alice")
    assert _resolve_user_id() == "alice"
