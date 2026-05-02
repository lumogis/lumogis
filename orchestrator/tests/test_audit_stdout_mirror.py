# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the write_audit / log_action stdout-mirror story (D7 / D7a).

Plan ``structured_audit_logging`` D7: every successful ``write_audit``
call emits exactly one ``audit.executed`` structured event with
cross-reference fields; every failure emits exactly one
``audit.write_failed``. NO payload bodies (input_summary,
result_summary, reverse_token, reverse_action) ever appear in stdout.

Plan D7a: ``permissions.log_action`` is intentionally unchanged — it
remains DB-only because every tool call goes through it and mirroring
would dwarf application logs.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import permissions
from actions import audit as audit_module
from models.actions import AuditEntry
from structlog.testing import capture_logs

import config


def _make_entry(**overrides) -> AuditEntry:
    """Build an AuditEntry with sensible defaults; overrides win."""
    base = dict(
        user_id="alice",
        action_name="filesystem-mcp.write_note",
        connector="filesystem-mcp",
        mode="DO",
        input_summary="path=/tmp/foo content=hello",
        result_summary="ok",
        reverse_action=None,
        executed_at=datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return AuditEntry(**base)


# ---------------------------------------------------------------------------
# Successful audit write — one structured event with the documented fields
# ---------------------------------------------------------------------------


class TestWriteAuditMirror:
    def test_emits_exactly_one_audit_executed_event(self, monkeypatch):
        ms = config.get_metadata_store()
        monkeypatch.setattr(ms, "fetch_one", lambda q, p=None: {"id": 4242})

        with capture_logs() as cap:
            result = audit_module.write_audit(_make_entry(), reverse_token="rev_token")

        assert result == 4242
        executed = [e for e in cap if e.get("event") == "audit.executed"]
        assert len(executed) == 1, (
            f"Expected exactly one audit.executed event; got {len(executed)} from {cap!r}"
        )

    def test_event_carries_all_documented_fields(self, monkeypatch):
        ms = config.get_metadata_store()
        monkeypatch.setattr(ms, "fetch_one", lambda q, p=None: {"id": 17})

        with capture_logs() as cap:
            audit_module.write_audit(
                _make_entry(user_id="bob", connector="caldav", mode="DO"),
                reverse_token="rev_xyz",
            )

        evt = next(e for e in cap if e.get("event") == "audit.executed")
        assert evt["audit_id"] == 17
        assert evt["user_id"] == "bob"
        assert evt["action_name"] == "filesystem-mcp.write_note"
        assert evt["connector"] == "caldav"
        assert evt["mode"] == "DO"
        assert evt["is_reversible"] is True

    def test_is_reversible_false_when_no_reverse_token(self, monkeypatch):
        ms = config.get_metadata_store()
        monkeypatch.setattr(ms, "fetch_one", lambda q, p=None: {"id": 1})

        with capture_logs() as cap:
            audit_module.write_audit(_make_entry(), reverse_token=None)

        evt = next(e for e in cap if e.get("event") == "audit.executed")
        assert evt["is_reversible"] is False

    def test_event_does_not_include_payload_bodies(self, monkeypatch):
        """D7: input_summary / result_summary / reverse_token / reverse_action
        must never appear in stdout — they live in the DB row."""
        ms = config.get_metadata_store()
        monkeypatch.setattr(ms, "fetch_one", lambda q, p=None: {"id": 99})

        sensitive_entry = _make_entry(
            input_summary="email_body_with_pii=very-private",
            result_summary="server_response_with_secrets=token12345",
            reverse_action={"undo": "secret-data"},
        )
        with capture_logs() as cap:
            audit_module.write_audit(sensitive_entry, reverse_token="rev_super_secret")

        evt = next(e for e in cap if e.get("event") == "audit.executed")
        for forbidden in (
            "input_summary",
            "result_summary",
            "reverse_action",
            "reverse_token",
        ):
            assert forbidden not in evt, (
                f"D7 violation: '{forbidden}' must NEVER appear on the stdout "
                f"audit.executed event (payload bodies stay in DB). Found: {evt!r}"
            )

    def test_no_event_when_db_returns_no_row(self, monkeypatch):
        """If the INSERT...RETURNING somehow yields no row, the
        function returns None and emits no audit.executed event."""
        ms = config.get_metadata_store()
        monkeypatch.setattr(ms, "fetch_one", lambda q, p=None: None)

        with capture_logs() as cap:
            result = audit_module.write_audit(_make_entry(), reverse_token=None)

        assert result is None
        assert not any(e.get("event") == "audit.executed" for e in cap)

    def test_failure_emits_exactly_one_audit_write_failed(self, monkeypatch):
        ms = config.get_metadata_store()

        def _boom(*a, **kw):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(ms, "fetch_one", _boom)

        with capture_logs() as cap:
            result = audit_module.write_audit(_make_entry(), reverse_token=None)

        assert result is None
        failures = [e for e in cap if e.get("event") == "audit.write_failed"]
        assert len(failures) == 1
        evt = failures[0]
        assert evt["error"] == "RuntimeError"
        assert "connection refused" in evt["message"]
        # Must not include payload bodies even in failure path.
        for forbidden in (
            "input_summary",
            "result_summary",
            "reverse_action",
            "reverse_token",
        ):
            assert forbidden not in evt, (
                f"audit.write_failed must not leak payload bodies; found '{forbidden}'."
            )


# ---------------------------------------------------------------------------
# permissions.log_action stays DB-only (D7a)
# ---------------------------------------------------------------------------


class TestLogActionStaysQuiet:
    def test_log_action_does_not_emit_any_structlog_event(self, monkeypatch):
        """D7a: every tool call goes through log_action; mirroring it to
        stdout would dwarf application logs. Must remain DB-only."""
        ms = config.get_metadata_store()
        # Make execute a no-op (the default MockMetadataStore.execute is
        # already a no-op, but be explicit so this test's intent is
        # obvious).
        monkeypatch.setattr(ms, "execute", lambda q, p=None: None)

        with capture_logs() as cap:
            permissions.log_action(
                connector="filesystem-mcp",
                action_type="read_note",
                mode="ASK",
                allowed=True,
                user_id="alice",
                input_summary="any",
                result_summary="any",
                reverse_action=None,
            )

        # Filter to events that look like they came from permissions.
        structlog_events = [
            e
            for e in cap
            if str(e.get("event", "")).startswith("permission")
            or str(e.get("event", "")).startswith("action")
        ]
        assert structlog_events == [], (
            f"permissions.log_action must NOT emit structlog events (D7a). "
            f"Got: {structlog_events!r}"
        )
