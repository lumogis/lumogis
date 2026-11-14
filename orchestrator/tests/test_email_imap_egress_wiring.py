# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-625 — the offline wiring guard passes on the committed tree, and fails
loudly if the email-IMAP Squid ACL lines are removed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_GUARD = _ROOT / "scripts" / "check_email_imap_egress_wiring.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("_imap_wiring_guard", _GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_guard_passes_on_committed_tree():
    guard = _load_guard()
    assert guard.check() == []


def test_guard_flags_missing_squid_acl(monkeypatch, tmp_path):
    guard = _load_guard()
    # Point the guard at a squid.conf stripped of the LUM-625 lines.
    fake_squid = tmp_path / "squid.conf"
    fake_squid.write_text("http_access deny all\n", encoding="utf-8")
    fake_allow = tmp_path / "lumogis-email-imap.txt"
    fake_allow.write_text("# empty\n", encoding="utf-8")
    monkeypatch.setattr(guard, "_SQUID", fake_squid)
    monkeypatch.setattr(guard, "_ALLOW", fake_allow)
    problems = guard.check()
    assert problems and all("missing required LUM-625 line" in p for p in problems)
