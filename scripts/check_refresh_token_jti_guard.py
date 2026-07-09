#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fail if obsolete ``refresh_token_jti`` string appears in orchestrator prod code.

The column was dropped in ``postgres/migrations/037-drop-users-refresh-token-jti.sql``
(LUM-244), retiring the LUM-29 / ADR 041 dual-write window. This guard stays in
place to prevent any new production code from referencing the now-nonexistent
column. ``orchestrator/tests`` is excluded (mocks may still model historical SQL).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent / "orchestrator"
    needle = "refresh_token_jti"
    hits: list[Path] = []
    for p in root.rglob("*.py"):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "tests":
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        txt = raw.decode("utf-8", errors="replace")
        if needle in txt:
            hits.append(p)
    if hits:
        print("Forbidden refresh_token_jti references outside orchestrator/tests:", file=sys.stderr)
        for h in hits:
            print(f"  {h}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
