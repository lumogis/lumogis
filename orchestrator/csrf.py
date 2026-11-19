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
   has not pinned a canonical origin. The request is then judged against
   the **derived origin** (LUM-981): when an ``Origin`` header is present it
   must equal the request's scheme plus ``Host`` (port kept; the scheme comes
   from ``X-Forwarded-Proto`` only when the immediate peer is in
   ``LUMOGIS_TRUSTED_PROXIES``), or ``https://`` plus the same ``Host`` (a TLS
   terminator in front of Caddy reports ``http`` while the browser's Origin is
   ``https``). A request with **no** ``Origin`` header is allowed: browsers
   always send ``Origin`` on cross-site POSTs, and the refresh cookie is
   ``SameSite=Strict``, which is then the only CSRF guard for requests that
   carry only a ``Referer``. A secure-address host (``*.homes.lumogis.app``,
   LUM-331) is different: the Origin must always equal ``https://`` + Host,
   because households are same-site with each other.
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

    if not _peer_is_trusted(request):
        return peer
    networks = _trusted_proxy_networks()

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


def _peer_is_trusted(request: Request) -> bool:
    """True when the immediate peer is in ``LUMOGIS_TRUSTED_PROXIES``.

    False for a missing client, an unparseable peer address, an empty list and
    an unresolvable proxy hostname (the fail-safe direction).
    """
    if request.client is None:
        return False
    try:
        peer_addr = ipaddress.ip_address(request.client.host or "")
    except ValueError:
        return False
    try:
        networks = _trusted_proxy_networks()
    except Exception:  # noqa: BLE001 — fail closed: trust nothing we could not resolve
        _log.exception("csrf: trusted proxy lookup failed; ignoring forwarded headers")
        return False
    return any(peer_addr in net for net in networks)


def _trusted_forwarded_proto(request: Request) -> str | None:
    """``http`` or ``https`` from ``X-Forwarded-Proto``, only from a trusted peer.

    The first comma-separated value, stripped and lower-cased. ``None`` for an
    untrusted peer, a missing header or any other value.
    """
    if not _peer_is_trusted(request):
        return None
    first = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return first if first in ("http", "https") else None


def _request_scheme(request: Request) -> str:
    """The scheme the browser used: trusted ``X-Forwarded-Proto``, else the socket's."""
    return _trusted_forwarded_proto(request) or request.url.scheme


def _origin_for(scheme: str, hostport: str) -> str:
    """``scheme://host[:port]`` as a browser would write the Origin.

    Lower-cased and stripped, and the default port (``:80`` for http, ``:443``
    for https) is dropped. Any other port is kept. A trailing ``.`` on the host
    is **kept**: browsers keep it in ``Origin`` (``http://lumogis.lan.``), so
    dropping it here would refuse every same-origin POST on such a host. One
    normaliser for the derived origin and the LUM-331 secure zone rule.
    """
    h = hostport.strip().lower().rstrip("/")
    if h.startswith("["):
        end = h.find("]")
        name, port = (h[: end + 1], h[end + 1 :].lstrip(":")) if end != -1 else (h, "")
    else:
        name, _, port = h.partition(":") if h.count(":") == 1 else (h, "", "")
    if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
        port = ""
    return f"{scheme}://{name}" + (f":{port}" if port else "")


def _derived_origin(request: Request) -> str:
    """The origin this request was addressed to: scheme plus ``Host``."""
    return _origin_for(_request_scheme(request), request.headers.get("Host", ""))


def require_same_origin(request: Request) -> None:
    """FastAPI dependency: refuse cross-site cookie-authenticated browser writes.

    * **Secure-address hosts** (any ``*.homes.lumogis.app`` name, LUM-331): the
      ``Origin`` must equal ``https://`` + the request's ``Host``, whatever
      ``LUMOGIS_PUBLIC_ORIGIN`` says. Households share the registrable domain
      ``lumogis.app`` (no Public Suffix List entry), so they are *same-site* with
      each other and ``SameSite=Strict`` cookies alone do not separate them. The
      rule needs no state: it holds even if the secure-address settings and the
      served Caddy config disagree.
    * **Every other host:** ``Origin == LUMOGIS_PUBLIC_ORIGIN`` when that is set
      (exact, unchanged). With nothing pinned: a present ``Origin`` must equal the
      derived origin (scheme plus ``Host``) or ``https://`` plus the same ``Host``;
      an absent ``Origin`` passes (LUM-981).

    Returns ``None`` on success; raises ``HTTPException(403)`` on mismatch. The
    bypasses (safe methods, Bearer, auth off) apply to both branches; see the
    module docstring.
    """
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

    # Dependency-free (no I/O, no project imports), so safe on every write.
    from services.home_dns.validate import is_secure_zone_host

    host = request.headers.get("Host", "")
    if is_secure_zone_host(host):
        expected = _origin_for("https", host)
        if presented.lower() != expected:
            _log.warning(
                "csrf: secure-address Origin mismatch path=%s presented=%r expected=%r",
                request.url.path,
                presented,
                expected,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="origin mismatch",
            )
        return None

    expected = _public_origin()
    if not expected:
        # No pinned origin (LUM-981): judge a present Origin against the address
        # the request used. No Origin at all is not a browser cross-site write
        # (browsers always send it), so it passes; SameSite=Strict covers the rest.
        if not presented:
            return None
        derived = _derived_origin(request)
        if presented.lower() in (derived, _origin_for("https", host)):
            return None
        _log.warning(
            "csrf: Origin mismatch path=%s presented=%r expected=%r",
            request.url.path,
            presented,
            derived,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="origin mismatch",
        )

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


# ---------------------------------------------------------------------------
# Setup claim host guard (LUM-981)
# ---------------------------------------------------------------------------

# Private-name suffixes: the true mirror of the *name* half of
# ``docker/caddy/preflight.sh`` (``lumogis_host_is_private``): ``localhost`` and
# ``*.localhost``, ``*.local``, ``*.lan``, ``*.home.arpa``, ``*.internal``,
# ``*.test``, ``*.ts.net``, plus single-label names. Pinned both ways by
# ``tests/test_csrf_derived_origin.py::test_H3``; change both lists together.
PRIVATE_NAME_SUFFIXES: tuple[str, ...] = (
    "localhost",
    "local",
    "lan",
    "home.arpa",
    "internal",
    "test",
    "ts.net",
)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_private_name(host: str) -> bool:
    """True for a *name* that cannot be a public internet name.

    ``localhost``, anything under a private suffix, and single-label names.
    An IP literal is not a name: it returns False (``claim_host_allowed``
    accepts IP literals separately). Empty returns False.
    """
    from services.home_dns.validate import host_without_port

    h = host_without_port(host)
    if not h or _is_ip_literal(h):
        return False
    if "." not in h:
        return True  # single label: never in public DNS
    return any(h.endswith("." + suffix) for suffix in PRIVATE_NAME_SUFFIXES)


def claim_host_allowed(request: Request) -> bool:
    """Whether ``POST /api/v1/setup/claim`` may be served for this ``Host``.

    DNS rebinding makes ``Origin == Host`` worthless for a cookie-less
    endpoint, so the claim also requires a ``Host`` an attacker page cannot
    rebind to: any IP literal, a private name, a secure-address name (the Home
    DNS Worker accepts RFC1918 targets only), or the host of a pinned
    ``LUMOGIS_PUBLIC_ORIGIN``. A missing or empty ``Host`` is refused.
    """
    from services.home_dns.validate import host_without_port
    from services.home_dns.validate import is_secure_zone_host

    raw = request.headers.get("Host", "")
    host = host_without_port(raw)
    if not host:
        return False
    if _is_ip_literal(host) or is_private_name(host) or is_secure_zone_host(raw):
        return True
    pinned = _public_origin()
    if pinned:
        netloc = pinned.split("://", 1)[-1].split("/", 1)[0]
        return host == host_without_port(netloc)
    return False
