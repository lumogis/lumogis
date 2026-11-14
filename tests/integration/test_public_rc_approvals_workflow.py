# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Approvals façade — seeded denied row + connector mode flip (RC compose).

The seeded row is ``smtp`` / ``email_send``: the only kind of denial the RC
stack can produce (a write by a registered action connector under ASK). Read-only
tool connectors such as ``filesystem-mcp`` never produce a denial and Approvals
refuses to flip them. The capability-connector flip is proven offline in
``orchestrator/tests/test_api_v1_approvals.py`` (LUM-962).
"""

from __future__ import annotations

import contextlib

import pytest

pytestmark = pytest.mark.integration

_CONNECTOR = "smtp"
_ACTION_TYPE = "email_send"


@pytest.mark.public_rc
def test_pending_lists_seed_then_set_do_mode(api):
    # ASK baseline through the API (invalidates Core's in-process permission cache).
    # Load-bearing: it heals a run killed between the flip and the ``finally`` below,
    # because the compose volumes survive ``cmd_down``.
    reset = api.delete(f"/api/v1/me/permissions/{_CONNECTOR}")
    assert reset.status_code == 200, reset.text[:800]
    assert reset.json().get("mode") == "ASK"

    try:
        # limit=200 so later denials cannot push the seed row out of the default 50.
        pr = api.get("/api/v1/approvals/pending?limit=200")
        assert pr.status_code == 200
        pending = pr.json().get("pending") or []
        markers = [
            x
            for x in pending
            if x.get("kind") == "denied_action"
            and x.get("input_summary") == "RC_APPROVALS_SEED_MARKER"
        ]
        assert markers, "run scripts/seed-public-rc-approvals-fixture.sh / compose cmd_up seed"
        row = markers[0]
        assert row.get("connector") == _CONNECTOR
        assert row.get("action_type") == _ACTION_TYPE
        assert row.get("suggested_action") == "set_connector_do"
        assert row.get("elevation_eligible") is True

        mode_r = api.post(
            f"/api/v1/approvals/connector/{_CONNECTOR}/mode",
            json={"mode": "DO"},
        )
        assert mode_r.status_code == 200, mode_r.text[:800]
        assert mode_r.json().get("mode") == "DO"

        perm = api.get(f"/api/v1/me/permissions/{_CONNECTOR}")
        assert perm.status_code == 200
        assert perm.json().get("mode") == "DO"
    finally:
        # Leave the ASK default for later tests in the session. Never assert here:
        # a teardown failure must not replace the real assertion above.
        with contextlib.suppress(Exception):
            api.delete(f"/api/v1/me/permissions/{_CONNECTOR}")
