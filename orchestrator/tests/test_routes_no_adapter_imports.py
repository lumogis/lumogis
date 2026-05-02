# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Architecture guard: ``orchestrator/routes/`` must not import ``adapters`` directly.

Routes depend on :mod:`services` (and ports via services); :mod:`adapters` are
constructed from :mod:`config` in the service/adapter layer. This test fails on
`import adapters` and `from adapters...` in route modules so drift is caught in
CI (see ``architecture_import_boundary_tests`` in the self-hosted remediation plan).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROUTES_DIR = Path(__file__).resolve().parent.parent / "routes"


def _route_modules():
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(_ROUTES_DIR.glob("*.py"))]


def _adapter_imports_in_source(relative_name: str, source: str) -> list[str]:
    tree = ast.parse(source, filename=relative_name)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                n = alias.name
                if n == "adapters" or n.startswith("adapters."):
                    line = node.lineno
                    violations.append(f"line {line}: import {n!r}")
        if isinstance(node, ast.ImportFrom):
            m = node.module
            if m is not None and (m == "adapters" or m.startswith("adapters.")):
                line = node.lineno
                violations.append(f"line {line}: from {m} import ...")
    return violations


@pytest.mark.parametrize("path_source", _route_modules(), ids=lambda p: p[0].name)
def test_route_module_does_not_import_adapters(path_source):
    path, source = path_source
    v = _adapter_imports_in_source(path.name, source)
    assert not v, f"{path.name} must not import adapters: " + "; ".join(v)
