# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""One LIKE-wildcard escape for every query that matches on text it did not write.

Pair the result with ``LIKE %s ESCAPE '\\'`` (or ``ILIKE``) and pass it as a
parameter. Callers add their own ``%`` anchors (infix search, prefix match).
"""

from __future__ import annotations


def escape_like(raw: str) -> str:
    r"""Escape ``\``, ``%`` and ``_`` for ``LIKE … ESCAPE '\'``.

    Backslash is escaped FIRST; doing ``%`` or ``_`` first would then double
    escape the backslashes it introduced and corrupt the pattern.
    """
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
