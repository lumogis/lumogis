#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""CI guard: LUM-613 must not import `tethered` (LUM-619 owns re-adding it).

LUM-613 (capability sandbox + egress scaffolding) deliberately ships with NO
`tethered.scope()` composition — the two low-value defense-in-depth pieces were
deferred to LUM-619 pending a written verification of `tethered==0.5.1`
process-global-hook concurrency/nesting behaviour. This guard fails if any of
LUM-613's owned files reintroduce a `tethered` import, so a later PR can't
silently undo that decision.

Scope: the files LUM-613 creates/modifies in the capability-egress path. The
existing `egress_guard.py` (LUM-553) legitimately imports `tethered` and is NOT
checked here.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# LUM-613-owned files (must stay tethered-free). egress_guard.py is intentionally
# excluded — it is LUM-553's and legitimately uses tethered.
CHECKED = [
    "orchestrator/services/capability_egress.py",
    "orchestrator/services/capability_registry.py",
    "orchestrator/services/unified_tools.py",
    "orchestrator/plugins/__init__.py",
    "orchestrator/models/capability.py",
]

_IMPORT_RE = re.compile(r"^\s*(import\s+tethered|from\s+tethered\b)", re.MULTILINE)


def main() -> int:
    offenders: list[str] = []
    for rel in CHECKED:
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _IMPORT_RE.search(text):
            offenders.append(rel)
    if offenders:
        print("check_no_tethered_lum613: FAIL — tethered import found in LUM-613 files:")
        for o in offenders:
            print(f"  - {o}")
        print("The tethered.scope() DiD is deferred to LUM-619 — do not re-add it here.")
        return 1
    print(f"check_no_tethered_lum613: OK — {len(CHECKED)} files are tethered-free")
    return 0


if __name__ == "__main__":
    sys.exit(main())
