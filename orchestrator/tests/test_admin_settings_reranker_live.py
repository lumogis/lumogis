# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-159: GET /settings reranker live vs desired fields."""

from __future__ import annotations

import pytest
from tests.test_admin_settings_legacy_keys_disabled import _client  # noqa: E402
from tests.test_admin_settings_legacy_keys_disabled import _mint_admin_jwt  # noqa: E402
from tests.test_admin_settings_legacy_keys_disabled import _seed_admin  # noqa: E402
from tests.test_admin_settings_legacy_keys_disabled import auth_on_env  # noqa: E402, F401
from tests.test_admin_settings_legacy_keys_disabled import store  # noqa: E402, F401


@pytest.mark.parametrize(
    ("env_backend", "store_value", "expected_live", "expected_enabled", "expected_pending"),
    [
        ("bge", "false", "bge", False, True),
        ("none", "false", "none", False, False),
        ("none", "true", "none", True, True),
        ("bge", "true", "bge", True, False),
    ],
)
def test_get_settings_reranker_live_and_pending(
    store,  # noqa: F811
    auth_on_env,  # noqa: F811
    monkeypatch,
    env_backend,
    store_value,
    expected_live,
    expected_enabled,
    expected_pending,
):
    monkeypatch.setenv("RERANKER_BACKEND", env_backend)
    store.app_settings["reranker_enabled"] = store_value
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.get("/settings", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reranker_backend_live"] == expected_live
    assert body["reranker_enabled"] is expected_enabled
    assert body["reranker_pending_restart"] is expected_pending
