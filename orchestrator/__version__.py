# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Lumogis Core version constant.

Single source of truth for the running Core version, importable as:
    from __version__ import __version__

IMPORTANT: this value MUST stay in lockstep with the `version` field in
pyproject.toml. When bumping the project version, update BOTH files in
the same commit. There is currently no automated check enforcing this —
a CI guard or PEP 621 dynamic-version setup is a future improvement.

Used by the capability-service registry (Area 2) to compare incoming
manifests' `min_core_version` against the running Core via
packaging.version.Version.
"""

# Keep in sync with pyproject.toml [project].version
__version__ = "0.3.0rc1"
