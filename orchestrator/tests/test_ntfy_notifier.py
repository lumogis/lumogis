# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for adapters/ntfy_notifier.py.

Pins per-user resolution behavior introduced in the ADR 018 ntfy
migration:

* :meth:`NtfyNotifier.notify` resolves URL / topic / token per call
  via :func:`services.ntfy_runtime.load_ntfy_runtime_config` (no
  process-global env caching).
* ``connector_not_configured`` and ``credential_unavailable`` from
  the loader produce a graceful ``return False`` (no raise), with
  logs carrying the domain code.
* HTTP success / non-2xx / network errors map to True / False / False
  respectively.
* Authorization header only set when the resolved ``token`` is
  non-empty.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services import connector_credentials as ccs


@pytest.fixture
def patched_loader(monkeypatch):
    """Patch :func:`load_ntfy_runtime_config` and capture calls.

    The adapter imports the symbol directly via
    ``from services.ntfy_runtime import load_ntfy_runtime_config``
    so we patch the binding on the adapter module, not the source.
    """
    captured: dict = {}

    def _set(impl):
        captured["impl"] = impl
        monkeypatch.setattr(
            "adapters.ntfy_notifier.load_ntfy_runtime_config",
            impl,
        )

    return _set


@pytest.fixture
def mock_httpx(monkeypatch):
    """Patch ``httpx.post`` on the adapter and capture invocations."""
    posts: list[dict] = []

    def _fake_post(url, content, headers, timeout):
        posts.append({"url": url, "content": content, "headers": dict(headers), "timeout": timeout})
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr("adapters.ntfy_notifier.httpx.post", _fake_post)
    return posts


def test_notify_success_posts_with_resolved_config(patched_loader, mock_httpx):
    patched_loader(lambda uid: {"url": "http://ntfy.lan:8088", "topic": uid, "token": "tok"})
    from adapters.ntfy_notifier import NtfyNotifier

    n = NtfyNotifier()
    ok = n.notify("hello", "world", priority=0.8, user_id="alice")

    assert ok is True
    assert len(mock_httpx) == 1
    sent = mock_httpx[0]
    assert sent["url"] == "http://ntfy.lan:8088/alice"
    assert sent["headers"]["Authorization"] == "Bearer tok"
    assert sent["headers"]["Title"] == "hello"
    assert sent["headers"]["Priority"] == "4"  # priority 0.8 → high


def test_notify_omits_auth_header_when_no_token(patched_loader, mock_httpx):
    patched_loader(lambda uid: {"url": "http://ntfy:80", "topic": "t", "token": ""})
    from adapters.ntfy_notifier import NtfyNotifier

    n = NtfyNotifier()
    n.notify("t", "m", priority=0.5, user_id="alice")
    assert "Authorization" not in mock_httpx[0]["headers"]


def test_notify_returns_false_on_connector_not_configured(patched_loader, mock_httpx):
    def _raise(uid):
        raise ccs.ConnectorNotConfigured("no row")

    patched_loader(_raise)
    from adapters.ntfy_notifier import NtfyNotifier

    ok = NtfyNotifier().notify("t", "m", priority=0.5, user_id="alice")
    assert ok is False
    assert mock_httpx == []


def test_notify_returns_false_on_credential_unavailable(patched_loader, mock_httpx):
    def _raise(uid):
        raise ccs.CredentialUnavailable("bad ciphertext")

    patched_loader(_raise)
    from adapters.ntfy_notifier import NtfyNotifier

    ok = NtfyNotifier().notify("t", "m", priority=0.5, user_id="alice")
    assert ok is False
    assert mock_httpx == []


def test_notify_returns_false_on_non_2xx(patched_loader, monkeypatch):
    patched_loader(lambda uid: {"url": "http://ntfy:80", "topic": "t", "token": ""})

    def _fake_post(url, content, headers, timeout):
        resp = MagicMock()
        resp.status_code = 500
        return resp

    monkeypatch.setattr("adapters.ntfy_notifier.httpx.post", _fake_post)

    from adapters.ntfy_notifier import NtfyNotifier

    assert NtfyNotifier().notify("t", "m", priority=0.5, user_id="alice") is False


def test_notify_returns_false_on_network_error(patched_loader, monkeypatch):
    patched_loader(lambda uid: {"url": "http://ntfy:80", "topic": "t", "token": ""})

    def _raise(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("adapters.ntfy_notifier.httpx.post", _raise)

    from adapters.ntfy_notifier import NtfyNotifier

    assert NtfyNotifier().notify("t", "m", priority=0.5, user_id="alice") is False


def test_priority_mapping():
    from adapters.ntfy_notifier import NtfyNotifier

    assert NtfyNotifier._map_priority(0.95) == 5
    assert NtfyNotifier._map_priority(0.8) == 4
    assert NtfyNotifier._map_priority(0.5) == 3
    assert NtfyNotifier._map_priority(0.1) == 1
