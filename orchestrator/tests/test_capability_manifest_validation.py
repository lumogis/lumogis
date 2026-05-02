# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fixture-level :class:`CapabilityManifest` validation (audit: manifest hardening in CI).

Broader contract tests live in :mod:`test_capability`. This module adds a
minimal **valid** + **invalid** pair so manifest shape rejections are pinned
independently of the registry. JSON Schema for ``input_schema`` at manifest
ingest is still a deliberate non-goal in :mod:`services.capability_registry`
until product needs it.
"""

from __future__ import annotations

import pytest
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityTransport
from pydantic import ValidationError

_VALID_MIN = {
    "name": "test-cap",
    "id": "test.cap",
    "version": "1.0.0",
    "type": "service",
    "transport": "http",
    "license_mode": "community",
    "maturity": "preview",
    "description": "Test manifest fixture.",
    "tools": [
        {
            "name": "dummy.tool",
            "description": "n/a",
            "license_mode": "community",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    ],
    "health_endpoint": "/health",
    "capabilities_endpoint": "/capabilities",
    "permissions_required": [],
    "config_schema": {"type": "object"},
    "min_core_version": "0.3.0rc1",
    "maintainer": "test",
}

_INVALID_MISSING_ID = {k: v for k, v in _VALID_MIN.items() if k != "id"}


def test_valid_capability_manifest_dict_roundtrips() -> None:
    m = CapabilityManifest.model_validate(_VALID_MIN)
    assert m.id == "test.cap"
    assert m.transport is CapabilityTransport.HTTP
    assert len(m.tools) == 1
    assert m.tools[0].license_mode is CapabilityLicenseMode.COMMUNITY


def test_invalid_capability_manifest_rejects_missing_id() -> None:
    with pytest.raises(ValidationError) as exc:
        CapabilityManifest.model_validate(_INVALID_MISSING_ID)
    assert "id" in str(exc.value).lower()
