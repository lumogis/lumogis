#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Fail a live test lane when its junit report holds skips or too few tests.

A skipped test proves nothing, so an opt-in proof lane (``make e2e-ingest-live``,
``make test-graph-parity``) treats every skip as a failure. This guard does not
judge failures or errors: pytest's own exit code does.

usage: junit_skip_guard.py <junit.xml> [--min-tests N]

Exit 0: no skips and at least N testcases (default 1).
Exit 1: a skip, or fewer than N testcases.
Exit 2: the file is missing or not parseable junit XML.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _parse(argv: list[str]) -> tuple[Path, int] | None:
    args = list(argv)
    min_tests = 1
    if "--min-tests" in args:
        i = args.index("--min-tests")
        try:
            min_tests = int(args[i + 1])
        except (IndexError, ValueError):
            return None
        del args[i : i + 2]
    if len(args) != 1:
        return None
    return Path(args[0]), min_tests


def main(argv: list[str]) -> int:
    parsed = _parse(argv)
    if parsed is None:
        print("usage: junit_skip_guard.py <junit.xml> [--min-tests N]", file=sys.stderr)
        return 2
    path, min_tests = parsed
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"junit_skip_guard: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    total = failed = errors = 0
    skipped: list[str] = []
    for case in root.iter("testcase"):
        total += 1
        if case.find("failure") is not None:
            failed += 1
        if case.find("error") is not None:
            errors += 1
        skip = case.find("skipped")
        if skip is not None:
            skipped.append(
                f"{case.get('classname', '')}::{case.get('name', '')} — {skip.get('message', '')}"
            )
    passed = total - failed - errors - len(skipped)
    print(
        f"ingest-live junit: tests={total} passed={passed} failed={failed} "
        f"errors={errors} skipped={len(skipped)}"
    )
    for line in skipped:
        print(line)
    if skipped:
        return 1
    if total < min_tests:
        print(f"junit_skip_guard: only {total} tests ran; expected at least {min_tests}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
