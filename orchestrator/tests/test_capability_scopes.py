# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capability permission-scope helpers + predicate (LUM-612, LUM-507 pillar a)."""

from __future__ import annotations

import pytest

from services import capability_scopes as cs


class _Manifest:
    def __init__(self, permissions_required):
        self.permissions_required = permissions_required


def test_capability_connector_identity():
    assert cs.capability_connector("mock-echo") == "capability.mock-echo"
    assert cs.is_capability_connector("capability.x")
    assert not cs.is_capability_connector("filesystem-mcp")
    assert cs.capability_id_from_connector("capability.mock-echo") == "mock-echo"
    # non-prefixed input returned as-is
    assert cs.capability_id_from_connector("filesystem-mcp") == "filesystem-mcp"


@pytest.mark.parametrize(
    "cap_id,ok",
    [
        ("mock-echo", True),
        ("lumogis.mock.echo", True),
        ("lumogis-graph", True),
        ("com.example.weather", True),
        ("MyNotes", False),  # uppercase
        ("has space", False),
        ("x" * 60, False),  # too long (>43)
        ("", False),
        (".leading-dot", False),
    ],
)
def test_is_grantable_capability_id(cap_id, ok):
    assert cs.is_grantable_capability_id(cap_id) is ok


def test_required_scopes_for_normalises_and_dedupes():
    m = _Manifest(["memory:read", "memory:read", " memory:write "])
    assert cs.required_scopes_for(m) == ["memory:read", "memory:write"]
    assert cs.required_scopes_for(_Manifest([])) == []
    assert cs.required_scopes_for(_Manifest(None)) == []


def test_malformed_required_scopes_flags_ungrantable():
    # clean: resource:action shape, both lowercase snake segments
    assert cs.malformed_required_scopes(_Manifest(["memory:read", "kg:write"])) == []
    assert cs.malformed_required_scopes(_Manifest([])) == []
    assert cs.malformed_required_scopes(_Manifest(None)) == []
    # malformed: no colon / empty segment / uppercase / extra colon / spaces
    assert cs.malformed_required_scopes(_Manifest(["memoryread"])) == ["memoryread"]
    assert cs.malformed_required_scopes(_Manifest(["memory:"])) == ["memory:"]
    assert cs.malformed_required_scopes(_Manifest([":read"])) == [":read"]
    assert cs.malformed_required_scopes(_Manifest(["Memory:Read"])) == ["Memory:Read"]
    assert cs.malformed_required_scopes(_Manifest(["a:b:c"])) == ["a:b:c"]
    # mixed → only the bad one is returned
    assert cs.malformed_required_scopes(_Manifest(["memory:read", "bad"])) == ["bad"]
