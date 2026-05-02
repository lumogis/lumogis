#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Emit `export KEY=value` lines for docker-style env files so shell callers can
# `eval "$(python3 scripts/rc_test_env_defaults.py path/to/env)"`.
#
# Only emits variables that are unset or empty in os.environ (override wins).

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        out[key] = val.strip().strip('"').strip("'")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: rc_test_env_defaults.py <env-file>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"rc_test_env_defaults.py: not a file: {path}", file=sys.stderr)
        return 2
    defaults = _parse_env_file(path)
    for key in sorted(defaults):
        if os.environ.get(key):
            continue
        print(f"export {shlex.quote(key)}={shlex.quote(defaults[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
