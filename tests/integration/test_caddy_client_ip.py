# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live proof that Caddy, not the client, decides the rate-limit IP (LUM-887).

Run with the same-origin stack up, from inside the compose network::

    make caddy-client-ip-prove

Every request below carries a different spoofed ``X-Forwarded-For``. Caddy
trusts no upstream by default and hands Core exactly one value (its own view of
the client), so all of them land in ONE per-IP bucket and the request after the
limit gets 429. With the old ``private_ranges`` trust, every request minted a
fresh bucket and none was ever limited.

If Caddy is unreachable the test **skips**, unless
``LUMOGIS_CADDY_CLIENT_IP_PROVE=1`` is set, in which case it **fails**
(same contract as ``test_caddy_security_headers.py``).
"""

from __future__ import annotations

import os
import socket

import httpx
import pytest

pytestmark = pytest.mark.integration

WEB_BASE = os.environ.get("LUMOGIS_WEB_BASE_URL", "http://caddy").rstrip("/")
PROVE = os.environ.get("LUMOGIS_CADDY_CLIENT_IP_PROVE", "").lower() in ("1", "true", "yes")

# orchestrator/routes/altcha.py:24 `_challenge_limiter` allows 30 requests per
# minute per IP and is checked before any auth or config branch, so it works on
# any stack. If that limit changes, update N.
N = 31


def _is_tcp_open(url: str) -> bool:
    u = httpx.URL(url)
    scheme = (u.scheme or "http").lower()
    port = u.port if u.port is not None else (443 if scheme == "https" else 80)
    try:
        with socket.create_connection((u.host, port), timeout=2.0):
            return True
    except OSError:
        return False


def test_spoofed_xff_through_caddy_shares_one_bucket() -> None:
    if not _is_tcp_open(WEB_BASE):
        msg = f"Caddy not reachable at {WEB_BASE}"
        if PROVE:
            pytest.fail(msg)
        pytest.skip(msg)

    statuses: list[int] = []
    with httpx.Client(timeout=10.0, verify=False) as client:  # noqa: S501 - local stack, may be tls internal
        for i in range(1, N + 1):
            r = client.get(
                f"{WEB_BASE}/api/v1/altcha/challenge",
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            )
            statuses.append(r.status_code)

    assert statuses[-1] == 429, (
        f"request {N} was not rate limited: statuses={statuses}. A spoofed "
        "X-Forwarded-For minted a fresh bucket per request. If the altcha "
        "limit changed, update N."
    )
