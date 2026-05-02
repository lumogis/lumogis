# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Approvals façade — seeded denied row + connector mode flip (RC compose)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_CONNECTOR = "filesystem-mcp"


@pytest.mark.public_rc
def test_pending_lists_seed_then_set_do_mode(api):
    pr = api.get("/api/v1/approvals/pending")
    assert pr.status_code == 200
    pending = pr.json().get("pending") or []
    markers = [
        x
        for x in pending
        if x.get("kind") == "denied_action"
        and x.get("input_summary") == "RC_APPROVALS_SEED_MARKER"
    ]
    assert markers, "run scripts/seed-public-rc-approvals-fixture.sh / compose cmd_up seed"

    mode_r = api.post(
        f"/api/v1/approvals/connector/{_CONNECTOR}/mode",
        json={"mode": "DO"},
    )
    assert mode_r.status_code == 200, mode_r.text[:800]
    assert mode_r.json().get("mode") == "DO"

    perm = api.get(f"/api/v1/me/permissions/{_CONNECTOR}")
    assert perm.status_code == 200
    assert perm.json().get("mode") == "DO"
