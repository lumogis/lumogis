# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Assert Caddy emits the Phase 1 security headers for the Lumogis front door.

Run with the same-origin stack up. From the **host** (Caddy on port 80)::

    docker compose up -d
    export LUMOGIS_WEB_BASE_URL=http://127.0.0.1
    cd orchestrator && python3 -m pytest ../tests/integration/test_caddy_security_headers.py -m integration -q

From **inside the compose network** (e.g. ``make web-caddy-headers``), the default
is ``LUMOGIS_WEB_BASE_URL=http://caddy`` so the one-shot ``orchestrator`` test
container reaches the Caddy service by DNS name, not container loopback.

If Caddy is unreachable, the test **skips** unless
``LUMOGIS_CADDY_HEADER_PROVE=1`` is set, in which case it **fails** (for CI
proof runs).

**HSTS:** required only when the base URL is ``https:``. For plain ``http:``
(local ``CADDY_DOMAIN=:80``) we require **no** HSTS (RFC 6797 — HSTS is a TLS
feature; our Caddyfile only sends it on ``protocol https``).
"""

from __future__ import annotations

import os
import socket

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.public_rc]

WEB_BASE = os.environ.get("LUMOGIS_WEB_BASE_URL", "http://127.0.0.1").rstrip("/")
PROVE = os.environ.get("LUMOGIS_CADDY_HEADER_PROVE", "").lower() in (
    "1",
    "true",
    "yes",
)


def _is_tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


@pytest.fixture
def caddy_reachable() -> bool:
    """True if the front door (host:port) accepts TCP, without requiring HTTP 200."""
    u = httpx.URL(WEB_BASE)
    host = u.host
    if not host:  # pragma: no cover
        return False
    scheme = (u.scheme or "http").lower()
    port = u.port if u.port is not None else (443 if scheme == "https" else 80)
    return _is_tcp_open(host, port)


def _h(resp: httpx.Response, name: str) -> str:
    for k, v in resp.headers.items():
        if k.lower() == name:
            return v
    return ""


def test_caddy_required_security_headers(caddy_reachable: bool) -> None:
    if not caddy_reachable:
        if PROVE:
            pytest.fail(
                f"Caddy not accepting TCP for {WEB_BASE!r}. "
                "Start the stack: docker compose up -d",
            )
        pytest.skip("Caddy / stack not reachable; start docker compose for this test.")

    with httpx.Client(base_url=WEB_BASE, timeout=30.0) as c:
        try:
            r = c.get("/")
        except httpx.RequestError as e:
            if PROVE:
                pytest.fail(f"GET {WEB_BASE}/ failed: {e}")
            pytest.skip(f"GET {WEB_BASE}/ failed: {e}")

    assert r.status_code == 200
    csp = _h(r, "content-security-policy")
    xcto = _h(r, "x-content-type-options")
    refpol = _h(r, "referrer-policy")
    perms = _h(r, "permissions-policy")
    hsts = _h(r, "strict-transport-security")

    assert csp, "Content-Security-Policy must be set by Caddy"
    assert "default-src" in csp
    assert xcto.lower() == "nosniff" or "nosniff" in xcto.lower()
    assert refpol, "Referrer-Policy must be set"
    assert perms, "Permissions-Policy must be set"
    is_https = WEB_BASE.lower().startswith("https://")
    if is_https:
        assert hsts, "Strict-Transport-Security must be present for HTTPS base URL"
        assert "max-age" in hsts.lower()
    else:
        assert not hsts, (
            "Strict-Transport-Security must not be set on cleartext HTTP (got HSTS; "
            "Caddy should only set it for protocol https)"
        )
