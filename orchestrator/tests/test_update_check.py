# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the LUM-187 update-availability check (services.update_check)."""

from __future__ import annotations

import pytest
import services.update_check as uc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "LUMOGIS_UPDATE_CHECK_ENABLED",
        "LUMOGIS_UPDATE_REPO",
        "LUMOGIS_UPDATE_CHECK_TIMEOUT",
        "LUMOGIS_UPDATE_CHECK_CACHE_TTL_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)
    uc.clear_release_cache_for_tests()


def _patch_release(monkeypatch, *, tag, url="https://example.com/r"):
    monkeypatch.setattr(
        uc, "fetch_latest_release", lambda repo, timeout: {"tag_name": tag, "html_url": url}
    )


def test_update_available_when_remote_newer(monkeypatch):
    monkeypatch.setattr(uc, "CORE_VERSION", "0.3.0rc1")
    _patch_release(monkeypatch, tag="v0.4.0")
    res = uc.build_update_status_response()
    assert res.checked is True
    assert res.update_available is True
    assert res.current_version == "0.3.0rc1"
    assert res.latest_version == "v0.4.0"
    assert res.release_url == "https://example.com/r"
    assert res.error is None


def test_no_update_when_equal(monkeypatch):
    monkeypatch.setattr(uc, "CORE_VERSION", "0.4.0")
    _patch_release(monkeypatch, tag="v0.4.0")
    res = uc.build_update_status_response()
    assert res.checked is True
    assert res.update_available is False


def test_no_update_when_remote_older(monkeypatch):
    monkeypatch.setattr(uc, "CORE_VERSION", "0.5.0")
    _patch_release(monkeypatch, tag="0.4.0")
    res = uc.build_update_status_response()
    assert res.checked is True
    assert res.update_available is False


def test_prerelease_is_older_than_final(monkeypatch):
    # PEP 440: 0.3.0rc1 < 0.3.0 — a final release of the same number is an update.
    monkeypatch.setattr(uc, "CORE_VERSION", "0.3.0rc1")
    _patch_release(monkeypatch, tag="v0.3.0")
    res = uc.build_update_status_response()
    assert res.update_available is True


def test_disabled_returns_unchecked(monkeypatch):
    monkeypatch.setenv("LUMOGIS_UPDATE_CHECK_ENABLED", "0")
    monkeypatch.setattr(uc, "CORE_VERSION", "0.3.0rc1")

    # fetch must not even be called when disabled
    def _boom(repo, timeout):
        raise AssertionError("network must not be called when disabled")

    monkeypatch.setattr(uc, "fetch_latest_release", _boom)
    res = uc.build_update_status_response()
    assert res.checked is False
    assert res.update_available is False
    assert "disabled" in (res.error or "")


def test_network_error_is_failsoft(monkeypatch):
    monkeypatch.setattr(uc, "CORE_VERSION", "0.3.0rc1")

    def _raise(repo, timeout):
        raise RuntimeError("boom")

    monkeypatch.setattr(uc, "fetch_latest_release", _raise)
    res = uc.build_update_status_response()
    assert res.checked is False
    assert res.update_available is False
    assert "RuntimeError" in (res.error or "")
    assert res.current_version == "0.3.0rc1"


def test_missing_tag_is_failsoft(monkeypatch):
    monkeypatch.setattr(uc, "CORE_VERSION", "0.3.0rc1")
    monkeypatch.setattr(uc, "fetch_latest_release", lambda repo, timeout: {"tag_name": ""})
    res = uc.build_update_status_response()
    assert res.checked is False
    assert "tag_name" in (res.error or "")


def test_non_pep440_tag_reports_without_update(monkeypatch):
    monkeypatch.setattr(uc, "CORE_VERSION", "0.3.0rc1")
    _patch_release(monkeypatch, tag="nightly-banana")
    res = uc.build_update_status_response()
    assert res.checked is True
    assert res.update_available is False
    assert res.latest_version == "nightly-banana"
    assert "non-PEP440" in (res.error or "")


def test_repo_env_is_passed(monkeypatch):
    monkeypatch.setenv("LUMOGIS_UPDATE_REPO", "acme/widgets")
    seen = {}

    def _capture(repo, timeout):
        seen["repo"] = repo
        return {"tag_name": "v9.9.9"}

    monkeypatch.setattr(uc, "fetch_latest_release", _capture)
    uc.build_update_status_response()
    assert seen["repo"] == "acme/widgets"


def test_release_lookup_uses_cache_within_ttl(monkeypatch):
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tag_name": "v1.0.0", "html_url": "https://example.com/r"}

    def _get(url, **kwargs):
        calls["n"] += 1
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setenv("LUMOGIS_UPDATE_CHECK_CACHE_TTL_SECONDS", "3600")
    now = {"t": 1000.0}
    monkeypatch.setattr(uc, "monotonic", lambda: now["t"])

    uc.fetch_latest_release("lumogis/lumogis", 4.0)
    uc.fetch_latest_release("lumogis/lumogis", 4.0)
    assert calls["n"] == 1

    now["t"] = 4601.0
    uc.fetch_latest_release("lumogis/lumogis", 4.0)
    assert calls["n"] == 2


def test_cache_disabled_when_ttl_zero(monkeypatch):
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tag_name": "v1.0.0"}

    def _get(url, **kwargs):
        calls["n"] += 1
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setenv("LUMOGIS_UPDATE_CHECK_CACHE_TTL_SECONDS", "0")

    uc.fetch_latest_release("lumogis/lumogis", 4.0)
    uc.fetch_latest_release("lumogis/lumogis", 4.0)
    assert calls["n"] == 2


def test_network_errors_are_not_cached(monkeypatch):
    calls = {"n": 0}

    def _get(url, **kwargs):
        calls["n"] += 1
        raise RuntimeError("boom")

    import httpx

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setenv("LUMOGIS_UPDATE_CHECK_CACHE_TTL_SECONDS", "3600")

    with pytest.raises(RuntimeError):
        uc.fetch_latest_release("lumogis/lumogis", 4.0)
    with pytest.raises(RuntimeError):
        uc.fetch_latest_release("lumogis/lumogis", 4.0)
    assert calls["n"] == 2
