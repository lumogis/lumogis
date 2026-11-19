# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Derived origin, refresh-cookie flag and claim host guard (LUM-981, Pass 6).

With ``LUMOGIS_PUBLIC_ORIGIN`` unset, ``require_same_origin`` judges a present
``Origin`` against scheme plus ``Host`` (C1-C8, C9c) and the refresh cookie's
``Secure`` flag follows the derived scheme and fails closed (C9, C9b). The
claim's rebinding guard (``claim_host_allowed``) is H1-H3; its route-level
behaviour (H4, C11) is in ``test_setup_routes.py``.
"""

from __future__ import annotations

import re

import csrf
import pytest
from csrf import PRIVATE_NAME_SUFFIXES
from csrf import _derived_origin
from csrf import _origin_for
from csrf import _peer_is_trusted
from csrf import _request_scheme
from csrf import _reset_trusted_proxy_caches_for_tests
from csrf import claim_host_allowed
from csrf import is_private_name
from csrf import require_same_origin
from fastapi import HTTPException
from routes.auth import _cookie_secure
from starlette.requests import Request
from tests.test_secret_sentinels import _REPO_ROOT

TRUSTED_PEER = "172.20.0.5"
OTHER_PEER = "203.0.113.9"


def _request(
    host: str | None = "192.168.1.20",
    *,
    origin: str | None = None,
    peer: str | None = OTHER_PEER,
    xfp: str | None = None,
    method: str = "POST",
    scheme: str = "http",
    authorization: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        headers.append((b"host", host.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if xfp is not None:
        headers.append((b"x-forwarded-proto", xfp.encode()))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "http",
        "method": method,
        "scheme": scheme,
        "path": "/api/v1/auth/refresh",
        "query_string": b"",
        "headers": headers,
        "client": (peer, 50000) if peer else None,
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("LUMOGIS_REFRESH_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("LUMOGIS_TRUSTED_PROXIES", TRUSTED_PEER)
    _reset_trusted_proxy_caches_for_tests()
    yield
    _reset_trusted_proxy_caches_for_tests()


def _refused(request: Request) -> bool:
    try:
        require_same_origin(request)
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "origin mismatch"
        return True
    return False


# --- derived origin ---------------------------------------------------------


def test_c1_matching_http_origin_on_a_lan_address_is_allowed():
    assert not _refused(_request("192.168.1.20", origin="http://192.168.1.20"))


def test_c2_mismatching_origin_is_403():
    assert _refused(_request("192.168.1.20", origin="http://evil.example"))
    assert _refused(_request("192.168.1.20", origin="http://192.168.1.21"))
    assert _refused(_request("192.168.1.20", origin="null"))


def test_c3_no_origin_is_allowed():
    assert not _refused(_request("192.168.1.20", origin=None))


def test_c4_trusted_https_proxy_allows_https_origin_and_refuses_http():
    base = {"peer": TRUSTED_PEER, "xfp": "https"}
    assert not _refused(_request("lumogis.example", origin="https://lumogis.example", **base))
    assert _refused(_request("lumogis.example", origin="http://lumogis.example", **base))


def test_c5_untrusted_peer_cannot_claim_https():
    req = _request("lumogis.example", origin="https://lumogis.example", xfp="https")
    assert _request_scheme(req) == "http"
    # The https-Origin rule (C9c) is what accepts this browser; the *derived*
    # origin itself stays http, which is what C5 pins.
    assert _derived_origin(req) == "http://lumogis.example"
    assert _refused(_request("lumogis.example", origin="https://evil.example", xfp="https"))


def test_c5_trusted_peer_scheme_comes_from_x_forwarded_proto():
    assert _request_scheme(_request("h", peer=TRUSTED_PEER, xfp="https")) == "https"
    assert _request_scheme(_request("h", peer=TRUSTED_PEER, xfp="HTTPS, http")) == "https"
    assert _request_scheme(_request("h", peer=TRUSTED_PEER, xfp="gopher")) == "http"
    assert _request_scheme(_request("h", peer=TRUSTED_PEER)) == "http"


def test_c6_a_pinned_origin_is_still_exact(monkeypatch):
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "http://localhost")
    assert _refused(_request("192.168.1.20", origin="http://192.168.1.20"))
    assert not _refused(_request("192.168.1.20", origin="http://localhost"))


def test_c7_the_secure_zone_rule_is_unchanged():
    host = "abcdefghijklmnopqrstuvwxyz.homes.lumogis.app"
    assert not _refused(_request(host, origin=f"https://{host}"))
    assert _refused(_request(host, origin=f"http://{host}"))
    assert _refused(_request(host, origin=None))  # no Origin is NOT allowed here


def test_c8_default_ports_are_dropped_and_other_ports_kept():
    assert _derived_origin(_request("lumogis.lan:80")) == "http://lumogis.lan"
    assert _derived_origin(_request("localhost:8090")) == "http://localhost:8090"
    assert _origin_for("https", "Lumogis.LAN.:443") == "https://lumogis.lan."
    assert _origin_for("http", "[fd00::1]:80") == "http://[fd00::1]"
    assert _origin_for("http", "[fd00::1]:8090") == "http://[fd00::1]:8090"


def test_c12_a_trailing_dot_host_matches_the_origin_the_browser_sends():
    """Browsers keep a trailing dot in ``Origin``; stripping it refused every POST."""
    assert not _refused(_request("lumogis.lan.:8090", origin="http://lumogis.lan.:8090"))
    assert _refused(_request("lumogis.lan.:8090", origin="http://lumogis.lan:8090"))
    zone = "abcdefghijklmnopqrstuvwxyz.homes.lumogis.app."
    assert not _refused(_request(zone, origin=f"https://{zone}"))


def test_c10_peer_trust_extraction_keeps_behaviour():
    assert _peer_is_trusted(_request(peer=TRUSTED_PEER))
    assert not _peer_is_trusted(_request(peer=OTHER_PEER))
    assert not _peer_is_trusted(_request(peer=None))


# --- https Origin behind a TLS terminator ------------------------------------


def test_c9c_https_origin_for_the_same_host_passes_behind_a_tls_terminator():
    """Tailscale Serve / a Cloudflare Tunnel in front of Caddy: Caddy reports http."""
    host = "x.tail1.ts.net"
    base = {"peer": TRUSTED_PEER, "xfp": "http"}
    assert not _refused(_request(host, origin=f"https://{host}", **base))
    assert _refused(_request(host, origin="http://evil.example", **base))
    assert _refused(_request(host, origin="https://evil.example", **base))


def test_an_http_origin_is_refused_when_the_derived_scheme_is_https():
    req = _request("x.example", origin="http://x.example", peer=TRUSTED_PEER, xfp="https")
    assert _refused(req)


# --- refresh cookie Secure flag ----------------------------------------------


def test_c9_cookie_secure_follows_a_trusted_proxy():
    assert _cookie_secure(_request("h", peer=TRUSTED_PEER, xfp="https")) is True
    assert _cookie_secure(_request("h", peer=TRUSTED_PEER, xfp="http")) is False


def test_c9_cookie_secure_fails_closed():
    # an untrusted peer reporting http
    assert _cookie_secure(_request("h", peer=OTHER_PEER, xfp="http")) is True
    # a trusted peer with no header
    assert _cookie_secure(_request("h", peer=TRUSTED_PEER)) is True
    # no request at all
    assert _cookie_secure(None) is True


def test_c9_cookie_secure_fails_closed_when_the_proxy_lookup_returns_nothing(monkeypatch):
    monkeypatch.setattr(csrf, "_trusted_proxy_networks", lambda: ())
    assert _cookie_secure(_request("h", peer=TRUSTED_PEER, xfp="http")) is True


def test_c9_cookie_secure_fails_closed_when_the_proxy_lookup_raises(monkeypatch):
    def boom():
        raise OSError("resolver down")

    monkeypatch.setattr(csrf, "_trusted_proxy_networks", boom)
    assert _cookie_secure(_request("h", peer=TRUSTED_PEER, xfp="http")) is True


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
def test_c9_cookie_secure_env_overrides(monkeypatch, value, expected):
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", value)
    assert _cookie_secure(_request("h", peer=TRUSTED_PEER, xfp="http")) is expected
    assert _cookie_secure(_request("h", peer=TRUSTED_PEER, xfp="https")) is expected


def test_c9_cookie_secure_on_a_secure_zone_host_even_when_env_says_false(monkeypatch):
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    assert _cookie_secure(_request("abcdefghijklmnopqrstuvwxyz.homes.lumogis.app")) is True


def test_c9b_a_pinned_https_origin_keeps_secure_even_when_the_proxy_reports_http(monkeypatch):
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.example.com")
    assert _cookie_secure(_request("lumogis.example.com", peer=TRUSTED_PEER, xfp="http")) is True


def test_c9b_an_https_origin_keeps_secure_when_the_proxy_reports_http():
    req = _request("x.ts.net", origin="https://x.ts.net", peer=TRUSTED_PEER, xfp="http")
    assert _cookie_secure(req) is True


# --- claim host guard --------------------------------------------------------


@pytest.mark.parametrize("host", ["192.168.1.20", "8.8.8.8", "[::1]", "[fd00::1]", "fd00::1"])
def test_h1_any_ip_literal_is_allowed_for_the_claim(host):
    assert claim_host_allowed(_request(host)) is True


@pytest.mark.parametrize("host", ["192.168.1.20", "8.8.8.8", "::1", "fd00::1", ""])
def test_h1_is_private_name_is_false_for_ip_literals_and_empty(host):
    assert is_private_name(host) is False


@pytest.mark.parametrize(
    "host",
    ["localhost", "a.localhost", "x.local", "x.lan", "x.home.arpa", "x.internal", "x.test"]
    + ["box.tail1.ts.net", "nas"],
)
def test_h1_private_names(host):
    assert is_private_name(host) is True


@pytest.mark.parametrize(
    "host",
    ["lumogis.example.com", "evil.example", "x.lan.evil.com", "ts.net.evil.com", "", "ts.net"],
)
def test_h1_public_names_are_not_private(host):
    assert is_private_name(host) is False
    assert claim_host_allowed(_request(host or None)) is False


@pytest.mark.parametrize(
    "host",
    ["LUMOGIS.LAN.", "[fd00::1]:8090", "fe80::1%eth0", "lumogis.lan:8090", "NAS:80"],
)
def test_h2_normalisation_allows(host):
    assert claim_host_allowed(_request(host)) is True


def test_h2_public_host_with_a_port_is_refused_unless_pinned(monkeypatch):
    assert claim_host_allowed(_request("lumogis.example.com:443")) is False
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.example.com")
    assert claim_host_allowed(_request("lumogis.example.com:443")) is True
    assert claim_host_allowed(_request("other.example.com")) is False


def test_h2_no_host_header_is_refused():
    assert claim_host_allowed(_request(None)) is False


def test_h2_the_secure_zone_is_allowed():
    assert claim_host_allowed(_request("abcdefghijklmnopqrstuvwxyz.homes.lumogis.app")) is True


def test_h3_private_name_suffixes_match_the_caddy_preflight_in_both_directions():
    text = (_REPO_ROOT / "docker" / "caddy" / "preflight.sh").read_text()
    case_line = next(
        ln for ln in text.splitlines() if "localhost" in ln and "*.local" in ln and "|" in ln
    )
    patterns = [p.strip().strip("'") for p in case_line.split(")")[0].split("|")]
    assert "localhost" in patterns, "preflight must list the bare name localhost"
    from_preflight = set()
    for pat in patterns:
        m = re.fullmatch(r"\*\.([a-z.]+)", pat)
        if m:
            from_preflight.add(m.group(1))
    assert from_preflight == set(PRIVATE_NAME_SUFFIXES)
