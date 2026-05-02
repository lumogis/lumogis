# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Origin-header CSRF check for cookie-authenticated browser writes.

Single responsibility: defence-in-depth on top of ``SameSite=Strict``
refresh cookies. ``SameSite=Strict`` is the primary defence (modern
browsers will not attach a strict cookie to a cross-site POST), but the
family-LAN plan §12 also called for an explicit ``Origin``-header check
to:

* fail closed on the small set of legacy browsers that don't honour
  ``SameSite=Strict``,
* refuse same-site requests that originate from an attacker-controlled
  document mounted at a different host on the same network,
* and produce an explicit, auditable refusal in the access log rather
  than silently relying on cookie scoping.

When this dependency is invoked, it returns ``None`` on success and
raises ``HTTPException(403)`` on mismatch.

Bypass conditions (intentional, narrow)
---------------------------------------
1. ``LUMOGIS_PUBLIC_ORIGIN`` env var is unset / empty — the deployment
   has not pinned a canonical origin yet (early bring-up). The
   ``SameSite=Strict`` cookie remains the only defence; we log nothing
   and pass through.
2. Request method is GET / HEAD / OPTIONS — these never mutate state and
   are not the CSRF surface.
3. ``Authorization: Bearer ...`` is present — Bearer tokens are not
   auto-attached by browsers. A CSRF attacker has no way to inject one,
   so the Origin check would only block legitimate non-browser callers
   (curl, MCP, the dashboard's own ``fetch`` flows that already use
   Bearer).
4. ``AUTH_ENABLED=false`` (single-user dev) — there are no real
   sessions to forge.

Bypass condition (4) is consulted lazily so this module does not import
``auth`` at module load time (avoids circular imports — ``auth.py``
already imports nothing from ``orchestrator``-level modules besides
``mcp_server`` lazily).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from functools import lru_cache

from fastapi import HTTPException, Request, status

_log = logging.getLogger(__name__)

_BYPASS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

def _public_origin() -> str:
    """Return the configured public origin (no trailing slash) or ``""``."""
    raw = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "").strip()
    return raw.rstrip("/")


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    """Parse ``LUMOGIS_TRUSTED_PROXIES`` into a tuple of network objects.

    Empty / unset → trust no proxies, so forwarded headers are ignored.
    Malformed entries are skipped with a WARNING — silently dropping a bad
    CIDR is safer than trusting it (which would defeat the whole point of
    the allowlist).
    """
    raw = os.environ.get("LUMOGIS_TRUSTED_PROXIES", "").strip()
    return _parse_trusted_proxies(raw)


@lru_cache(maxsize=8)
def _parse_trusted_proxies(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    items = [s.strip() for s in raw.split(",") if s.strip()]
    out: list[ipaddress._BaseNetwork] = []
    for item in items:
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            _log.warning("csrf: ignoring malformed LUMOGIS_TRUSTED_PROXIES entry %r", item)
    return tuple(out)


def _proxied_client_ip(request: Request) -> str:
    """Return the *real* client IP, honouring ``X-Forwarded-For`` only when
    the immediate peer is on the trusted-proxy allowlist.

    Resolution rules:

    * If ``request.client`` is ``None`` → ``"unknown"``.
    * If the immediate peer is **not** in ``LUMOGIS_TRUSTED_PROXIES`` →
      return ``request.client.host`` verbatim. The header is treated as
      attacker-controlled and ignored. This is the security-critical
      branch: it stops a malicious external client from spoofing the
      rate-limiter key by sending its own ``X-Forwarded-For``.
    * If the peer **is** trusted and ``X-Forwarded-For`` is present:
      walk the comma-separated list **right-to-left** through trusted
      hops, returning the first untrusted address (i.e. the original
      client). If the entire chain is trusted (e.g. a single-proxy
      deployment), the leftmost entry wins. Malformed entries fall
      back to ``request.client.host``.
    * If the peer is trusted but no ``X-Forwarded-For`` header is set,
      return ``request.client.host`` (direct call from the proxy).

    This is used by the per-IP login rate limiter to ensure that running
    behind Caddy / nginx does not collapse every request onto the proxy's
    own IP. See plan `cross_device_lumogis_web` §Security decisions →
    "Rate-limit collapse behind reverse proxy" and the matching D5
    finding in critique-round-5-gpt-5.4.md.
    """
    if request.client is None:
        return "unknown"
    peer = request.client.host or "unknown"

    networks = _trusted_proxy_networks()
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    if not any(peer_addr in net for net in networks):
        return peer

    raw_xff = request.headers.get("X-Forwarded-For", "").strip()
    if not raw_xff:
        # Fall back to X-Real-IP if the proxy uses that convention.
        raw_xff = request.headers.get("X-Real-IP", "").strip()
        if not raw_xff:
            return peer

    candidates = [c.strip() for c in raw_xff.split(",") if c.strip()]
    if not candidates:
        return peer

    # Walk right-to-left through trusted hops. The first untrusted
    # entry (or the leftmost entry if every hop is trusted) is the
    # real client.
    last_valid = peer
    for ip_str in reversed(candidates):
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return last_valid
        last_valid = ip_str
        if not any(addr in net for net in networks):
            return ip_str
    # All hops were trusted — return the leftmost (originator).
    return candidates[0]


def require_same_origin(request: Request) -> None:
    """FastAPI dependency: enforce ``Origin == LUMOGIS_PUBLIC_ORIGIN`` for
    cookie-authenticated browser writes.

    Returns ``None`` on success. Raises ``HTTPException(403)`` on
    mismatch. See module docstring for the bypass matrix.
    """
    expected = _public_origin()
    if not expected:
        # Deployment hasn't pinned an origin — fall back to SameSite=Strict only.
        return None

    if request.method in _BYPASS_METHODS:
        return None

    # Bearer-authenticated calls are not a CSRF surface (browsers do not
    # auto-attach Bearer headers). Skip the check rather than break the
    # MCP / dashboard / curl callers that legitimately use Bearer auth.
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return None

    # AUTH_ENABLED=false means no real sessions exist. Imported lazily
    # to avoid a circular dependency with auth.py.
    from auth import auth_enabled

    if not auth_enabled():
        return None

    presented = request.headers.get("Origin", "").strip().rstrip("/")
    if presented != expected:
        _log.warning(
            "csrf: Origin mismatch path=%s presented=%r expected=%r",
            request.url.path,
            presented,
            expected,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="origin mismatch",
        )
    return None
