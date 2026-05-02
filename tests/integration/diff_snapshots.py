#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Diff two `/graph/health` JSON snapshots; exit non-zero on parity break.

Used by the parity test (`test_graph_parity.py`) to compare the
inprocess-mode and service-mode FalkorDB state after ingesting an
identical fixture corpus. The contract under test is: switching
`GRAPH_MODE` MUST NOT change projection output.

Compared fields (must all be identical):
  * `nodes` — total node count
  * `edges` — total edge count
  * `nodes_by_label` (if present) — per-label counts
  * `edges_by_type` (if present) — per-type counts
  * `edge_score_buckets` (if present) — quality histogram

Fields IGNORED (not part of the parity contract):
  * `last_run_at`, `uptime_s`, `version`, anything timestamp-shaped —
    these are expected to differ and are noise.
  * Any extra field present in only one snapshot — emitted as an INFO
    line to stderr but does not fail the diff. (The two services run
    different versions of `/graph/health` for now; reconciliation of
    the response shape is out of scope for the extraction plan.)

Exit codes:
  0 — snapshots are identical on the compared fields.
  1 — at least one compared field differs.
  2 — argument or file-read error.

Output: machine-readable diff to stdout (JSON), human summary to
stderr.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_COMPARED_FIELDS = (
    "nodes",
    "edges",
    "nodes_by_label",
    "edges_by_type",
    "edge_score_buckets",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        print(f"diff_snapshots: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"diff_snapshots: {path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def _normalise(value: Any) -> Any:
    """Sort dict keys so equality is order-independent."""
    if isinstance(value, dict):
        return {k: _normalise(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return value
    return value


def diff(
    inprocess: dict[str, Any], service: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return (mismatches, info_messages)."""
    mismatches: dict[str, dict[str, Any]] = {}
    info: list[str] = []

    for field in _COMPARED_FIELDS:
        in_inprocess = field in inprocess
        in_service = field in service

        if not in_inprocess and not in_service:
            continue
        if in_inprocess != in_service:
            which = "inprocess" if in_inprocess else "service"
            info.append(
                f"field {field!r} present in {which} only — skipped"
            )
            continue

        a = _normalise(inprocess[field])
        b = _normalise(service[field])
        if a != b:
            mismatches[field] = {"inprocess": a, "service": b}

    return mismatches, info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inprocess", type=Path, help="snapshot from GRAPH_MODE=inprocess")
    parser.add_argument("service", type=Path, help="snapshot from GRAPH_MODE=service")
    args = parser.parse_args(argv)

    a = _load(args.inprocess)
    b = _load(args.service)

    mismatches, info = diff(a, b)

    for line in info:
        print(f"diff_snapshots: INFO: {line}", file=sys.stderr)

    if not mismatches:
        print(json.dumps({"status": "ok"}))
        return 0

    print(json.dumps({"status": "mismatch", "differences": mismatches}, indent=2))
    print(
        f"diff_snapshots: PARITY BROKEN — {len(mismatches)} field(s) differ: "
        f"{', '.join(mismatches)}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
