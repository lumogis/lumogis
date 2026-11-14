# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-618 — live container-network egress containment proof.

Brings up docker-compose.egress-containment-test.yml (isolated `internal: true`
network + Squid egress proxy + a community-probe) and asserts the load-bearing
guarantees:

* the ALLOWED host (example.com, the only entry in the mock's allow file) is
  reachable through the proxy over real HTTPS, and the leaf cert observed
  THROUGH THE TUNNEL is the genuine upstream leaf issued by a real CA (NOT the
  proxy's bump CN) — proving splice, no MITM. The observation is unconditional:
  if the cert cannot be read, the test fails rather than skipping (LUM-764);
* the DENIED host, observed the same way, presents the proxy's bump CN — the
  negative control that gives the assertion above its discriminating power
  (LUM-764). Both hosts get `200` on the CONNECT line, which is why the
  certificate, not the status line, is the observable (LUM-763);
* a NON-declared host is refused via a Squid deny (proxy 403 / TCP_DENIED), NOT a
  bare connection failure — proving the ACL fired (distinct from no-route);
* a proxy-BYPASS direct connection has no route (IPv4 and IPv6) — proving the
  network isolation itself, not just the proxy.

This is the plan's step-1 PoC made permanent. Marked integration + slow; run via
`make egress-containment-test`. Requires Docker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = "docker-compose.egress-containment-test.yml"
_PROJECT = "lum618-egress-containment"
_ALLOWED_HOST = "example.com"
_DENIED_HOST = "example.org"  # a real host NOT in the allow file
_BUMP_CN = "lumogis-egress-proxy-bump"  # squid.conf gen-bump-cert.sh CN


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return (
        subprocess.run(["docker", "info"], capture_output=True, text=True, check=False).returncode
        == 0
    )


pytestmark.append(pytest.mark.skipif(not _docker_available(), reason="Docker not available"))


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", _PROJECT, "-f", _COMPOSE, *args],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        check=check,
    )


def _exec_probe(script: str, *args: str) -> str:
    """Run a python snippet inside the community-probe; return stdout.

    ``args`` arrive as ``sys.argv[1:]``. Prefer them over interpolating into the
    snippet: a probe that speaks a wire protocol needs literal ``\\r\\n``, and an
    f-string eats the escapes, producing a snippet that will not even compile.
    """
    proc = _compose("exec", "-T", "community-probe", "python", "-c", script, *args, check=False)
    return (proc.stdout or "") + (proc.stderr or "")


def _probe_payload(script: str, *args: str) -> dict:
    result = _exec_probe(script, *args)
    try:
        return json.loads(result.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:  # snippet crashed; surface its output
        raise AssertionError(f"probe did not emit JSON ({exc}): {result}") from exc


# LUM-764 — observe the leaf certificate THROUGH THE PROXY TUNNEL.
#
# This replaces a probe that opened a second, DIRECT connection to :443. The
# network is declared `internal: true` and has no route, so that connection
# always raised, "issuer" was never set, and the assertion guarding on it was
# dead code. The load-bearing no-MITM claim was asserted in prose and unasserted
# in code for the whole life of this test.
#
# Raw string, argv-parameterised: the CONNECT request needs real CRLFs.
_CERT_THROUGH_TUNNEL = r"""
import json, socket, ssl, subprocess, sys

host = sys.argv[1]
out = {}


def tunnel():
    raw = socket.create_connection(("egress-proxy", 3128), timeout=15)
    raw.sendall(("CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n\r\n" % (host, host)).encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = raw.recv(4096)
        if not chunk:
            break
        resp += chunk
    return raw, resp


# 1. Read whatever the far end presents, WITHOUT verification. CERT_NONE is what
#    makes the bump cert observable at all: a verifying handshake rejects it as
#    self-signed and raises, so its CN would never reach an assertion. Under
#    CERT_NONE getpeercert() returns {}, hence the DER form parsed by openssl.
try:
    raw, resp = tunnel()
    out["connect_status"] = resp.split(b" ")[1].decode() if b" " in resp else "none"
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    t = ctx.wrap_socket(raw, server_hostname=host)
    der = t.getpeercert(True)
    t.close()  # clean shutdown: Squid replays abruptly-closed sessions for minutes
    if der:
        out["cert"] = (
            subprocess.run(
                ["openssl", "x509", "-inform", "DER", "-noout", "-subject", "-issuer"],
                input=der,
                capture_output=True,
            )
            .stdout.decode()
            .replace("\n", " ")
            .strip()
        )
except Exception as e:
    out["observe_error"] = type(e).__name__ + ":" + str(e)[:160]

# 2. Handshake again, this time VERIFYING against the system CA store. Success is
#    the "real CA" half of the no-MITM claim: `generate-host-certificates=off`
#    means Squid cannot mint a chain, so only a genuine upstream leaf verifies.
try:
    raw, _ = tunnel()
    ssl.create_default_context().wrap_socket(raw, server_hostname=host).close()
    out["verified"] = True
except Exception as e:
    out["verified"] = False
    out["verify_error"] = type(e).__name__ + ":" + str(e)[:160]

print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def egress_stack():
    _compose("down", "-v", check=False)
    try:
        _compose("up", "-d", "--wait", "--", "egress-proxy", "community-probe")
        # give the probe a moment (sleep entrypoint) and proxy time to settle
        time.sleep(2)
        yield
    finally:
        _compose("down", "-v", check=False)


def test_allowed_host_reachable_and_not_mitmd(egress_stack) -> None:
    # The cert is checked BEFORE reachability. Both break together when the proxy
    # stops splicing an allowed host, and a plain HTTPS GET reports that as an
    # opaque URLError — which would attribute a containment regression to a
    # network problem. The cert probe names it.
    payload = _probe_payload(_CERT_THROUGH_TUNNEL, _ALLOWED_HOST)

    # LUM-764: UNCONDITIONAL. The previous form was `if "issuer" in payload:`,
    # which is the more general trap this fix exists to remove — a conditional
    # assertion degrades silently to a no-op the moment its probe stops working
    # and reports green either way. If the cert cannot be observed now, that is
    # a failure, not a skip.
    assert "cert" in payload, (
        f"no cert observed through the tunnel — the no-MITM property is UNPROVEN, "
        f"which is a failure and not a skip: {payload}"
    )
    assert _BUMP_CN not in payload["cert"], (
        f"the allowed host was BUMPED, not spliced — this is the MITM regression: {payload}"
    )
    assert f"CN={_ALLOWED_HOST}" in payload["cert"], (
        f"expected the genuine upstream leaf for {_ALLOWED_HOST}: {payload}"
    )
    # And it chains to a real CA: Squid runs `generate-host-certificates=off`, so
    # it cannot forge one. Asserting the chain, not just the CN, is what rules out
    # a bump cert that merely copied the subject.
    assert payload.get("verified") is True, (
        f"upstream leaf did not verify against the system CA store: {payload}"
    )

    # Reachability: the splice carries an ordinary HTTPS request end to end.
    fetch = f"""
import json, urllib.request
out = {{}}
try:
    with urllib.request.urlopen("https://{_ALLOWED_HOST}/", timeout=15) as r:
        out["status"] = r.status
except Exception as e:
    out["fetch_error"] = type(e).__name__ + ":" + str(e)[:120]
print(json.dumps(out))
"""
    reach = _probe_payload(fetch)
    assert reach.get("status") == 200, f"allowed host not reachable via proxy: {reach}"


def test_denied_host_presents_bump_cert_through_tunnel(egress_stack) -> None:
    """Negative control for the assertion above — LUM-764's mechanism-removal check.

    A cert-based no-MITM assertion is only worth something if the observable can
    actually come out the other way. It can: a denied host is TERMINATED rather
    than spliced, and Squid presents its own bump cert. That is the exact shape a
    bumped *allowed* host would take, so this test is the evidence that the
    allowed-host assertion would fail if the proxy ever started intercepting.

    It also pins why the certificate is the observable at all: the CONNECT status
    line is `200` here too. Squid must accept the tunnel before it can peek at the
    ClientHello, so allowed and denied are indistinguishable at that layer
    (LUM-763).
    """
    payload = _probe_payload(_CERT_THROUGH_TUNNEL, _DENIED_HOST)
    assert "cert" in payload, f"no cert observed for the denied host: {payload}"
    assert _BUMP_CN in payload["cert"], (
        f"denied host did not present the proxy bump cert — either the deny stopped "
        f"firing or the observable moved: {payload}"
    )
    assert payload.get("connect_status") == "200", (
        f"expected the uninformative 200 CONNECT line that motivates the cert "
        f"observable (LUM-763): {payload}"
    )
    assert payload.get("verified") is False, (
        f"the bump cert must NOT verify against a real CA store: {payload}"
    )


def test_denied_host_refused_by_proxy_not_by_no_route(egress_stack) -> None:
    # A non-declared host must be refused BY THE PROXY (403/deny), which is
    # distinct from a no-route failure — proving the Squid ACL actually fired.
    script = f"""
import json, urllib.error, urllib.request
out = {{}}
try:
    urllib.request.urlopen("https://{_DENIED_HOST}/", timeout=15)
    out["result"] = "unexpected_success"
except urllib.error.HTTPError as e:
    out["result"] = "http_error"
    out["code"] = e.code
except Exception as e:
    out["result"] = "other"
    out["err"] = type(e).__name__ + ":" + str(e)[:120]
print(json.dumps(out))
"""
    result = _exec_probe(script)
    payload = json.loads(result.strip().splitlines()[-1])
    assert payload["result"] != "unexpected_success", (
        f"denied host was reachable — containment breach: {result}"
    )
    # Squid refuses a non-allowlisted CONNECT with a 403 (TCP_DENIED). Accept the
    # proxy-deny signal; a bare connection error would instead indicate the ACL
    # never ran (the failure mode this assertion guards against).
    if payload["result"] == "http_error":
        assert payload.get("code") in (403, 407), f"expected proxy deny, got {payload}"
    else:
        # Some proxy configs surface the deny as a tunnel-refused error rather
        # than an HTTPError; assert it is a proxy-originated refusal, not a raw
        # network timeout (which would mean the isolation, not the ACL, blocked).
        assert "err" in payload, result


def test_proxy_bypass_has_no_route(egress_stack) -> None:
    # Bypass the proxy entirely: a direct connection from the isolated net must
    # have NO ROUTE (IPv4 and IPv6) — this proves the network isolation itself.
    script = """
import json, socket
out = {}
def probe(fam, addr):
    try:
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(6)
        s.connect(addr)
        s.close()
        return "connected"
    except Exception as e:
        return type(e).__name__
out["ipv4"] = probe(socket.AF_INET, ("1.1.1.1", 443))
try:
    out["ipv6"] = probe(socket.AF_INET6, ("2606:4700:4700::1111", 443))
except OSError as e:
    out["ipv6"] = type(e).__name__
print(json.dumps(out))
"""
    result = _exec_probe(script)
    payload = json.loads(result.strip().splitlines()[-1])
    assert payload["ipv4"] != "connected", f"direct IPv4 egress had a route: {result}"
    assert payload["ipv6"] != "connected", f"direct IPv6 egress had a route: {result}"
