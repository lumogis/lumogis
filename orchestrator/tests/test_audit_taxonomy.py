# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for audit event_type taxonomy (LUM-197)."""

from __future__ import annotations

import json

from services.audit_taxonomy import ACTION_REVOKED
from services.audit_taxonomy import USER_INVITE_MINTED
from services.audit_taxonomy import action_names_for_event_type
from services.audit_taxonomy import build_description
from services.audit_taxonomy import derive_event_type


def test_derive_event_type_privacy_mode_block():
    assert (
        derive_event_type("privacy_mode_block", "llm", '{"decline_type": "external_call_denied"}')
        == "privacy.external_call.denied"
    )


def test_derive_event_type_user_invite_minted():
    assert derive_event_type(USER_INVITE_MINTED, "auth") == "auth.invite.minted"


def test_derive_event_type_mcp_token_revoked():
    assert derive_event_type(ACTION_REVOKED, "auth") == "auth.mcp_token.revoked"


def test_action_names_for_event_type_prefix():
    pred = action_names_for_event_type("auth.invite")
    assert pred is not None
    assert USER_INVITE_MINTED in pred.action_names
    assert "__user_invite__.redeemed" in pred.action_names


def test_action_names_for_event_type_action_executed():
    pred = action_names_for_event_type("action.executed")
    assert pred is not None
    assert pred.exclude_mapped is True
    assert not pred.action_names


def test_derive_event_type_unknown_fallback():
    assert derive_event_type("draft_email", "smtp") == "action.executed"


def test_derive_event_type_routine():
    assert derive_event_type("routine:weekly_review", "routines") == "action.routine.executed"


def test_build_description_from_json():
    row = {
        "action_name": "privacy_mode_block",
        "connector": "llm",
        "input_summary": json.dumps(
            {"requested_model": "gpt-4", "decline_type": "external_call_denied"}
        ),
        "result_summary": "",
    }
    desc = build_description(row)
    assert "requested_model=gpt-4" in desc
    assert "reverse_token" not in desc.lower()


def test_build_description_redacts_bearer():
    row = {
        "action_name": "test",
        "connector": "x",
        "input_summary": "Authorization: Bearer lin_api_secret123",
        "result_summary": None,
    }
    desc = build_description(row)
    assert "lin_api_" not in desc
    assert "Bearer" not in desc
