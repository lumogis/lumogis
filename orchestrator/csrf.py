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

Per-user backup and admin POST surfaces mount ``Depends(require_same_origin)``
(ADR 016). Un-skipping deferred CSRF blocking behaviour for
Bearer-authenticated calls on those routes is explicitly deferred to the
``cross_device_lumogis_web`` programme (cookie-session phase). Until then,
bypass #3 remains the intentional v1 contract; do not remove it without
updating ADR 016 and the paired regression tests that pin Bearer skip plus
non-Bearer enforcement in ``orchestrator/tests/test_user_export_routes.py``.

Bypass condition (4) is consulted lazily so this module does not import
``auth`` at module load time (avoids circular imports — ``auth.py``
already imports nothing from ``orchestrator``-level modules besides
``mcp_server`` lazily).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import threading
import time
from functools import lru_cache

from fastapi import HTTPException
from fastapi import Request
from fastapi import status

_log = logging.getLogger(__name__)

_BYPASS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _public_origin() -> str:
    """Return the configured public origin (no trailing slash) or ``""``."""
    raw = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "").strip()
    return raw.rstrip("/")


# RFC 1123 labels plus ``_``: compose service names may contain it and
# Docker's embedded DNS resolves them.
_LABEL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")

_HOST_TTL_SECONDS = 30.0
_HOST_NEGATIVE_TTL_SECONDS = 30.0
_now = time.monotonic  # module attribute so tests can patch the clock

# host -> (expiry, networks). Guarded by _HOST_LOCK; DNS never runs under it.
_HOST_CACHE: dict[str, tuple[float, tuple[ipaddress._BaseNetwork, ...]]] = {}
_HOST_INFLIGHT: set[str] = set()
_HOST_FAILING: set[str] = set()
_HOST_LOCK = threading.Lock()

_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_hostname_entry(item: str) -> bool:
    """True for a syntactically valid hostname that is not a numeric lookalike."""
    if "/" in item or ":" in item or len(item) > 253:
        return False
    if not any(c.isascii() and c.isalpha() for c in item):
        return False  # ``10.0.0.256`` stays malformed, not a "hostname"
    return _HOSTNAME_RE.match(item) is not None


def _classify(item: str) -> tuple[str, ipaddress._BaseNetwork | None]:
    """Return ``("static", net)``, ``("host", None)`` or ``("malformed", None)``."""
    try:
        return "static", ipaddress.ip_network(item, strict=False)
    except ValueError:
        pass
    if _is_hostname_entry(item):
        return "host", None
    return "malformed", None


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    """Return the networks whose ``X-Forwarded-For`` Core believes.

    ``LUMOGIS_TRUSTED_PROXIES`` is a comma-separated list of IPs, CIDRs or
    hostnames. Empty / unset -> trust no proxies, so forwarded headers are
    ignored. Malformed entries are skipped with a WARNING - silently dropping
    a bad CIDR is safer than trusting it (which would defeat the whole point
    of the allowlist).

    Hostnames (the compose default is ``caddy``) are resolved with
    ``socket.getaddrinfo`` and cached for 30 s, also after a failure. The
    lookup is single-flight and never runs under the lock: only the one
    thread that took the in-flight slot waits on DNS, every other request
    gets the last known value at once. An unresolvable name trusts nothing,
    and a failed refresh drops the previous answer. That is the fail-safe
    direction: after a Caddy recreate, for up to 30 s the forwarded header is
    ignored and requests key on Caddy's own address (one shared bucket).
    """
    raw = os.environ.get("LUMOGIS_TRUSTED_PROXIES", "").strip()
    if not raw:
        return ()
    nets = _parse_trusted_proxies(raw)
    for host in _parse_trusted_hostnames(raw):
        nets = nets + _resolve_trusted_host(host)
    return nets


def _warn_over_wide(net: ipaddress._BaseNetwork) -> None:
    if net.is_loopback:
        return
    private = net.is_private or (net.version == 4 and net.subnet_of(_CGNAT))
    limit = 24 if net.version == 4 else 64
    if private and net.prefixlen < limit:
        _log.warning(
            "csrf: LUMOGIS_TRUSTED_PROXIES entry %s trusts a whole private range; "
            "any host in it can choose its rate-limit IP. Behind the bundled Caddy "
            "remove it and use the default (caddy).",
            net,
        )


@lru_cache(maxsize=8)
def _parse_trusted_proxies(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    """Static IP / CIDR entries only (hostnames are handled separately)."""
    out: list[ipaddress._BaseNetwork] = []
    for item in (s.strip() for s in raw.split(",") if s.strip()):
        kind, net = _classify(item)
        if kind == "static":
            assert net is not None
            _warn_over_wide(net)
            out.append(net)
        elif kind == "malformed":
            _log.warning("csrf: ignoring malformed LUMOGIS_TRUSTED_PROXIES entry %r", item)
    return tuple(out)


@lru_cache(maxsize=8)
def _parse_trusted_hostnames(raw: str) -> tuple[str, ...]:
    """Hostname entries, lowercased and de-duplicated, in order."""
    out: list[str] = []
    for item in (s.strip() for s in raw.split(",") if s.strip()):
        if _classify(item)[0] == "host":
            low = item.lower()
            if low not in out:
                out.append(low)
    return tuple(out)


def _lookup(host: str) -> tuple[ipaddress._BaseNetwork, ...]:
    nets: list[ipaddress._BaseNetwork] = []
    for *_, sockaddr in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
        addr = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        net = ipaddress.ip_network(addr)
        if net not in nets:
            nets.append(net)
    return tuple(nets)


def _resolve_trusted_host(host: str) -> tuple[ipaddress._BaseNetwork, ...]:
    """Resolve ``host`` with a TTL cache. Single-flight; never raises."""
    with _HOST_LOCK:
        expiry, nets = _HOST_CACHE.get(host, (0.0, ()))
        if _now() < expiry or host in _HOST_INFLIGHT:
            return nets
        _HOST_INFLIGHT.add(host)
    try:
        try:
            fresh = _lookup(host)
            err: Exception | None = None
        except (OSError, UnicodeError, ValueError) as exc:
            fresh = ()
            err = exc
        with _HOST_LOCK:
            if err is None:
                _HOST_CACHE[host] = (_now() + _HOST_TTL_SECONDS, fresh)
                if host in _HOST_FAILING:
                    _HOST_FAILING.discard(host)
                    _log.info("csrf: trusted proxy host %r resolves again", host)
            else:
                _HOST_CACHE[host] = (_now() + _HOST_NEGATIVE_TTL_SECONDS, ())
                if host not in _HOST_FAILING:
                    _HOST_FAILING.add(host)
                    _log.warning(
                        "csrf: trusted proxy host %r did not resolve (%s); forwarded "
                        "headers from it are ignored until it does",
                        host,
                        err,
                    )
        return fresh
    finally:
        with _HOST_LOCK:
            _HOST_INFLIGHT.discard(host)


def _reset_trusted_proxy_caches_for_tests() -> None:
    _parse_trusted_proxies.cache_clear()
    _parse_trusted_hostnames.cache_clear()
    with _HOST_LOCK:
        _HOST_CACHE.clear()
        _HOST_INFLIGHT.clear()
        _HOST_FAILING.clear()


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
