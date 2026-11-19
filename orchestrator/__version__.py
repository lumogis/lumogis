# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Lumogis Core version constant.

Single source of truth for the running Core version, importable as:
    from __version__ import __version__

IMPORTANT: this value MUST stay in lockstep with the `version` field in
pyproject.toml. When bumping the project version, update BOTH files in
the same commit. `orchestrator/tests/test_version_lockstep.py` checks both.

Used by the capability-service registry (Area 2) to compare incoming
manifests' `min_core_version` against the running Core via
packaging.version.Version.
"""

# Keep in sync with pyproject.toml [project].version, and set it to the release
# version on private main before each public release: the admin update card
# compares it with the latest GitHub release, and release-assets.yml refuses a
# release whose tree reports another version (LUM-980). It stayed "0.3.0rc1"
# through 0.12.0 and 0.13.0, so those installs always saw "update available".
# Raising it never breaks a capability: manifests ask for min_core_version 0.3.0rc1.
__version__ = "0.14.0"
