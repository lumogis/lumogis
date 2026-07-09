# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the feature-flag registry (LUM-126)."""

from __future__ import annotations

import features
import pytest


def test_all_flags_default_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure no flag env vars leak in from the host.
    for key in features.all_flag_keys():
        monkeypatch.delenv(features.get_flag(key).resolved_env_var(), raising=False)
    assert features.enabled_flags() == set()
    for row in features.snapshot():
        assert row["enabled"] is False
        assert row["default"] is False


def test_env_var_naming_is_prefixed() -> None:
    for key in features.all_flag_keys():
        flag = features.get_flag(key)
        assert flag.resolved_env_var() == features.FLAG_ENV_PREFIX + key


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE", " On "])
def test_truthy_values_enable(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    key = features.all_flag_keys()[0]
    monkeypatch.setenv(features.get_flag(key).resolved_env_var(), raw)
    assert features.is_enabled(key) is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "", "maybe"])
def test_falsey_values_disable(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    key = features.all_flag_keys()[0]
    monkeypatch.setenv(features.get_flag(key).resolved_env_var(), raw)
    assert features.is_enabled(key) is False


def test_enabled_reflected_in_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    key = features.all_flag_keys()[0]
    monkeypatch.setenv(features.get_flag(key).resolved_env_var(), "true")
    snap = {row["key"]: row for row in features.snapshot()}
    assert snap[key]["enabled"] is True
    assert key in features.enabled_flags()


def test_unknown_flag_raises() -> None:
    with pytest.raises(features.UnknownFeatureFlag):
        features.is_enabled("NOT_A_REAL_FLAG")
    # UnknownFeatureFlag is a KeyError subclass so existing handlers still catch it.
    assert issubclass(features.UnknownFeatureFlag, KeyError)


def test_keys_are_sorted_and_unique() -> None:
    keys = features.all_flag_keys()
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
