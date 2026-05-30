# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Safe filesystem path prefix checks for ingest roots (multi-root boundaries)."""

from __future__ import annotations

import os
from pathlib import Path


def _resolved_path_under_root(resolved: Path, root: Path) -> bool:
    """True when *resolved* is *root* or strictly under *root* (not prefix siblings)."""
    root_resolved = root.expanduser().resolve(strict=False)
    path_resolved = resolved.expanduser().resolve(strict=False)
    root_str = str(root_resolved)
    path_str = str(path_resolved)
    return path_str == root_str or path_str.startswith(root_str + os.sep)


def _resolved_path_under_any_root(resolved: Path, roots: list[str] | list[Path]) -> bool:
    """True when *resolved* is under any path in *roots*."""
    if not roots:
        return False
    path_resolved = resolved.expanduser().resolve(strict=False)
    for root in roots:
        if _resolved_path_under_root(path_resolved, Path(root)):
            return True
    return False
