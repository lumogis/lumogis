# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Skip live HTTP tests when the target is not listening.

``make test-integration`` from the host against a household stack, and
``docker compose run orchestrator`` against ``127.0.0.1``, both produce
``ConnectError`` on graph/mock-capability/RC URLs. That is a missing
service, not a contract failure — the ``api`` fixture already skips this
way; tests that open their own client must too.
"""

from __future__ import annotations

import httpx
import pytest


def skip_unless_reachable(url: str, *, what: str) -> None:
    try:
        with httpx.Client(timeout=3.0) as client:
            client.get(url)
    except httpx.ConnectError as exc:
        pytest.skip(f"{what} not reachable at {url}: {exc}")
