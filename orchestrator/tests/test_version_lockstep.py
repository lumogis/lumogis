# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Core's version is one number in two files, and a release must carry it (LUM-980)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from __version__ import __version__
from packaging.version import Version

_ROOT = Path(__file__).resolve().parent.parent.parent


def test_version_matches_pyproject():
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__


def test_version_satisfies_every_bundled_min_core_version():
    """Raising Core's version must never lock out a capability shipped with it."""
    sources = [
        _ROOT / "services" / "lumogis-graph" / "routes" / "capabilities.py",
        _ROOT / "services" / "lumogis-mock-capability" / "main.py",
    ]
    found = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        found += re.findall(r'(?:_MIN_CORE_VERSION\s*=|"min_core_version":)\s*"([^"]+)"', text)
    assert found, "no bundled min_core_version found"
    for required in found:
        assert Version(__version__) >= Version(required), required


def test_release_assets_refuses_a_tree_with_another_version():
    """release-assets.yml checks orchestrator/__version__.py against the release version."""
    wf = (_ROOT / ".github" / "workflows" / "release-assets.yml").read_text(encoding="utf-8")
    assert "orchestrator/__version__.py" in wf
    assert "Core reports version" in wf
