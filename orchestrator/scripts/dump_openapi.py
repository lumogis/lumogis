# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Dump the live FastAPI OpenAPI spec to a deterministic JSON file.

Produced by plan ``cross_device_lumogis_web`` Pass 0.3 step 19. The
snapshot is committed at ``clients/lumogis-web/openapi.snapshot.json``
and consumed by:

* ``clients/lumogis-web/scripts/codegen.mjs`` — `openapi-typescript`
  drives `src/api/generated/`.
* ``orchestrator/tests/test_api_v1_openapi_snapshot.py`` — CI guard
  that fails when the live `/openapi.json` drifts from the snapshot.

Determinism rules (so a snapshot diff = a real change):

* Keys are sorted at every level (``sort_keys=True``).
* Two-space indent, UTF-8, trailing newline.
* The volatile ``info.version`` field is rewritten to a constant
  (``"snapshot"``) so a build-time git-sha suffix doesn't churn the
  snapshot on every commit.

Usage (repo root)::

    cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys \\
        --out ../clients/lumogis-web/openapi.snapshot.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def _build_openapi() -> dict[str, Any]:
    """Import :mod:`main` and return ``app.openapi()``.

    The lifespan auth-consistency gate is bypassed so the dumper can run
    without a real DB seed (same env hatch the test suite uses).
    """
    os.environ.setdefault(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION",
        "true",
    )
    os.environ.setdefault("RERANKER_BACKEND", "none")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    import main  # noqa: WPS433 — top-level import side-effects required.

    return main.app.openapi()


def _normalise(spec: dict[str, Any]) -> dict[str, Any]:
    """Strip volatile fields so the snapshot is reproducible."""
    info = spec.get("info") or {}
    if "version" in info:
        info["version"] = "snapshot"
    spec["info"] = info
    return spec


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true", help="indent=2")
    parser.add_argument("--sort-keys", action="store_true", help="alphabetic key order")
    parser.add_argument(
        "--out",
        default="-",
        help="output file (default '-' = stdout)",
    )
    args = parser.parse_args(argv)

    spec = _normalise(_build_openapi())

    payload = json.dumps(
        spec,
        indent=2 if args.pretty else None,
        sort_keys=bool(args.sort_keys),
        ensure_ascii=False,
    )
    if not payload.endswith("\n"):
        payload += "\n"

    if args.out == "-":
        sys.stdout.write(payload)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
