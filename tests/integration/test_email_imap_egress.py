# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-625 — live email-IMAP cooperative-audit egress proof (Layer 2).

Brings up docker-compose.egress-imap-test.yml (real squid.conf; allow dir mounted
from a temp dir so the committed tree is untouched) and asserts:

* Core (172.31.0.3, the `core_egress_src` ACL) may CONNECT to the DECLARED host
  on 993 — allowed, and *spliced*: it reaches the upstream server end-to-end;
* an UNDECLARED host on 993 is refused by the proxy, NOT merely unreachable —
  the undeclared server is up and would answer if the ACL let the request through;
* the DECLARED host on a NON-993 port is refused (port ACL);
* a CONNECT from the COMMUNITY source (172.31.0.10, a contained-capability stand-in)
  to the DECLARED host on 993 is refused — proving the P0 source-scope: the shared
  proxy does not grant contained community capabilities the email egress path.

THE OBSERVABLE IS THE PEER CERTIFICATE, NOT THE CONNECT STATUS LINE (LUM-763).
Under ``ssl_bump peek`` Squid answers ``200 Connection established`` to every
CONNECT — it must, to read the ClientHello — so the status line is identical for
allowed and denied. The certificate is not: a spliced connection yields the
upstream server's CN, a denied one yields the proxy's bump CN. See ``_peer_cn``.

Asserts *proxy* behaviour (the audit layer). Does NOT prove a dual-homed Core
cannot egress — that is Layer 1 (in-process).

Marked integration + slow; run via ``make egress-imap-test``. Requires Docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = "docker-compose.egress-imap-test.yml"
_PROJECT = "lum625-imap-egress"
# LUM-763: in-network TLS servers, not internet hosts. The lane needs something to
# splice TO — nothing listens on example.com:993, so the "allowed" case had no
# positive signal and the lane fell back to asserting the CONNECT status line,
# which under peek is always 200. These two run the same image and command and
# differ only in whether the allow file names them.
_DECLARED_HOST = "imap-declared.test"
_UNDECLARED_HOST = "imap-undeclared.test"
# The proxy's own cert (gen-bump-cert.sh:33). Seeing THIS means Squid answered
# instead of the upstream server — i.e. the request was denied.
_BUMP_CN = "lumogis-egress-proxy-bump"

# ── INVARIANT: never probe within one watcher poll of an allow-file rewrite ──
#
# LUM-758's watcher applies allow-file changes asynchronously. Until it reloads,
# Squid answers from the PREVIOUSLY loaded config — so a probe fired immediately
# after a rewrite reports the old policy, not the one under test.
#
# This is not hypothetical and it is not a Squid defect. LUM-765 reproduced it
# deterministically: allow the dotless `imap-declared`, narrow the file to the
# dotted name only, probe at once -> still SPLICED; probe a second later -> denied.
# That is what produced the "an unauthorised host was spliced once" observation
# during LUM-763 review, and chasing it as an SNI-matching bug would have been a
# dead end. It does not occur on a cold start (0/10) — there is no previous config.
#
# Any test that rewrites the allow file and then probes MUST wait this out first.
# Today's LUM-625 tests are safe by construction (the fixture writes the file
# before `compose up`, so no rewrite precedes them); this exists so that stays
# true deliberately rather than by luck.
_POLL_SECONDS = 1  # docker-compose.egress-imap-test.yml pins this
ALLOW_FILE_SETTLE = _POLL_SECONDS * 8  # a stated multiple of the poll interval


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return (
        subprocess.run(["docker", "info"], capture_output=True, text=True, check=False).returncode
        == 0
    )


# LUM-718 lesson, applied here too: a lane that skips silently reports green while
# validating nothing. The escalation logic in orchestrator/tests/integration/
# conftest.py is path-scoped to that directory and does not cover this tree, so CI
# sets LUMOGIS_REQUIRE_DOCKER=1 and a missing Docker becomes a hard failure.
if not _docker_available():
    if os.environ.get("LUMOGIS_REQUIRE_DOCKER") == "1":
        raise RuntimeError(
            "LUMOGIS_REQUIRE_DOCKER=1 but Docker is unavailable — this lane would "
            "have skipped silently and reported success."
        )
    pytestmark.append(pytest.mark.skipif(True, reason="Docker not available"))


def _compose(
    *args: str, env: dict | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", _PROJECT, "-f", _COMPOSE, *args],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def _peer_cn(service: str, host: str, port: int, *, sni: str | None = None) -> str:
    """CONNECT to ``host:port`` through the proxy, then read the peer certificate CN.

    **This replaces a status-line assertion that could not work.** Under
    ``ssl_bump peek`` Squid answers ``200 Connection established`` to *every*
    CONNECT — it must, because the SNI is unknowable until the client sends a
    ClientHello, which cannot happen until the tunnel is open. The old probe read
    that status line and exited without ever completing a handshake, so it returned
    the same answer for allowed and denied alike, and additionally logged a
    ``NONE``-class access-log entry that carried no information either.

    The certificate *does* vary, and it varies per connection:

    * **allowed** -> spliced end-to-end, so the CN is the **upstream server's**;
    * **denied**  -> Squid bumps the connection with ``bump.pem`` to deliver its
      error page over TLS, so the CN is ``lumogis-egress-proxy-bump``.

    (The bump cert comes from the ``http_access`` deny path, *not* from
    ``ssl_bump terminate``. ``terminate`` fires only on an SNI/authority mismatch
    and presents no certificate at all — an EOF mid-handshake, logged
    ``NONE_ABORTED``. None of the tests here take that branch.)

    ``getpeercert()`` returns ``{}`` under ``CERT_NONE``, so the DER form is read
    and parsed with ``openssl``, which ships in the probe image.
    """
    server_name = sni or host
    script = f"""
import socket, ssl, subprocess
s = socket.create_connection(('egress-proxy', 3128), timeout=10)
s.sendall(b'CONNECT {host}:{port} HTTP/1.1\\r\\nHost: {host}:{port}\\r\\n\\r\\n')
s.recv(256)
try:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    t = ctx.wrap_socket(s, server_hostname='{server_name}')
    der = t.getpeercert(True)
    t.close()                      # clean shutdown: Squid replays abruptly-closed
                                   # sessions for minutes, polluting the access log
    if not der:
        print('NOCERT')
    else:
        pem = ssl.DER_cert_to_PEM_cert(der)
        out = subprocess.run(['openssl', 'x509', '-noout', '-subject'],
                             input=pem, capture_output=True, text=True)
        print(out.stdout.strip() or 'PARSEFAIL')
except Exception as exc:
    print('ERR ' + type(exc).__name__)
"""
    proc = _compose("exec", "-T", service, "python", "-c", script, check=False)
    return ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-1]


def _denied(cn_line: str) -> bool:
    """Denied == the proxy answered us, not the upstream server."""
    return _BUMP_CN in cn_line


@pytest.fixture(scope="module")
def imap_egress_stack():
    # Temp allow dir — the committed tree is never mutated (crash-safe, parallel-safe).
    tmp = Path(tempfile.mkdtemp(prefix="lum625-allow-"))
    (tmp / "lumogis-email-imap.txt").write_text(
        f"# test fixture — declared host\n{_DECLARED_HOST}\n", encoding="utf-8"
    )
    # squid.conf also references the community mock allow file — copy the real
    # one so the shared config loads cleanly.
    src_allow = _ROOT / "docker" / "egress-proxy" / "allow"
    for f in src_allow.glob("*.txt"):
        if f.name != "lumogis-email-imap.txt":
            shutil.copy2(f, tmp / f.name)
    env = {**os.environ, "IMAP_EGRESS_ALLOW_DIR": str(tmp)}
    try:
        _compose("up", "-d", "--wait", env=env)
        # LUM-572: yield the allow dir so the LUM-758 watcher tests can rewrite it.
        # Existing tests take this fixture and ignore the value.
        yield tmp
    finally:
        _compose("down", "-v", env=env, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_declared_host_connect_allowed(imap_egress_stack):
    """Allowed means SPLICED — the client reaches the real server, end to end.

    Asserting the upstream CN (not merely "not denied") is what makes this
    falsifiable: remove the host from the allow file and this must fail. The old
    version asserted a 200 status line, which Squid returns for every CONNECT, so
    it would have passed for any host at all — including a denied one.
    """
    cn = _peer_cn("core-probe", _DECLARED_HOST, 993)
    assert _DECLARED_HOST in cn, (
        f"declared host should be spliced to the real server (CN={_DECLARED_HOST}), got: {cn!r}"
    )
    assert not _denied(cn), f"declared host was denied — the proxy answered instead: {cn!r}"


def test_undeclared_host_connect_denied(imap_egress_stack):
    cn = _peer_cn("core-probe", _UNDECLARED_HOST, 993)
    assert _denied(cn), f"undeclared host should be refused by the proxy, got: {cn!r}"
    # Not merely "something failed": the undeclared server is up and reachable, so
    # reaching ITS cert would mean the ACL let us through.
    assert _UNDECLARED_HOST not in cn, f"undeclared host was reached end-to-end: {cn!r}"


def test_nonstandard_port_connect_denied(imap_egress_stack):
    # Same host, same server, only the port differs — so a denial here is
    # attributable to `acl imaps_port port 993` and nothing else.
    cn = _peer_cn("core-probe", _DECLARED_HOST, 1993)
    assert _denied(cn), f"non-993 port should be refused, got: {cn!r}"


def test_community_source_denied_even_for_declared_host(imap_egress_stack):
    """P0 scope guard: a contained community capability must NOT gain email egress.

    Same target, same port, same allow file as the passing Core case — only the
    source IP differs (172.31.0.10 vs the `core_egress_src` 172.31.0.3), so the
    denial is attributable to source scoping alone.

    This also closes the open question LUM-572 raised: the community probe logged
    ``NONE/200`` rather than ``TCP_DENIED``, which looked like the source scope
    failing open. It was not. ``NONE/200`` is the CONNECT tunnel being accepted so
    the ClientHello can be peeked — every request gets it, allowed or not — and the
    deny lands on the bumped step afterwards. The bump cert asserted here is that
    deny, observed directly rather than inferred from a log line.
    """
    cn = _peer_cn("community-probe", _DECLARED_HOST, 993)
    assert _denied(cn), (
        f"community-source CONNECT to a declared host must be refused (P0 scope), got: {cn!r}"
    )
    assert _DECLARED_HOST not in cn, (
        f"community source reached the mail host end-to-end — LUM-625 source scope broken: {cn!r}"
    )


def test_unlisted_hostname_variant_is_denied(imap_egress_stack):
    """Negative control for the ACL's matching semantics.

    The bare (dotless) service name is NOT in the allow file; only the dotted alias
    is, so every probe here must be denied.

    **The intermittent splice that prompted this test is explained (LUM-765), and it
    was not an ACL defect.** Review saw the dotless name spliced once and denied on
    every later trial. That is ``ALLOW_FILE_SETTLE`` above: a probe fired inside the
    watcher's poll window after an allow-file rewrite is answered by the *previously
    loaded* config, and the file did briefly list the dotless name while the compose
    aliases were being iterated. Reproduced deterministically — allow the dotless
    name, narrow the file to the dotted name only, probe immediately: spliced; probe
    a second later: denied. It does **not** occur on a cold start (0 splices in 10
    consecutive cold starts, each probing the dotless name as the very first request
    through a freshly booted proxy) because there is no previous config to serve.

    So `ssl::server_name` matching is exact and correct, and this test guards that.
    Still repeated three times: the cost is trivial, and a single shot cannot
    distinguish "correct" from "got lucky" if the matching ever does regress.
    """
    for attempt in range(3):
        cn = _peer_cn("core-probe", "imap-declared", 993)
        assert _denied(cn), (
            f"a hostname absent from the allow file was reached (attempt {attempt + 1}): {cn!r}"
        )


# ── LUM-758 / LUM-572: the proxy applies its own allow file ──────────────────
#
# LUM-758 made the proxy watch its allow directory and reload itself, so a
# household member can add a mailbox without an operator. It shipped verified by a
# single manual run; these tests make that permanent. The watcher is load-bearing —
# if it regresses, a member's mailbox silently stops syncing, which is exactly the
# bug LUM-758 was filed to fix.
#
# PROVING A NEGATIVE NEEDS CARE. The only external signals are a log line and a
# heartbeat touched every tick regardless of outcome, so "sleep, then grep for
# absence" would pass identically when the watcher is simply DEAD. Therefore every
# negative assertion here is bracketed by a canary: immediately afterwards, make a
# real change and assert it IS applied. That is what distinguishes "correctly did
# not reload" from "was never running".

_ALLOW_FILE = "lumogis-email-imap.txt"
# The watcher tests' settle IS the module invariant — one value, so the two cannot
# drift apart and quietly disagree about how long "applied" takes (LUM-765).
_SETTLE = ALLOW_FILE_SETTLE


def _reload_count(env: dict) -> int:
    """How many times the watcher has actually reloaded Squid.

    A count, not the presence of text: 'did the log gain a line' is a question
    with a stable answer, 'is the line absent' is a race.
    """
    proc = _compose("logs", "egress-proxy", env=env, check=False)
    return ((proc.stdout or "") + (proc.stderr or "")).count("applied via SIGHUP")


def _write_allow(allow_dir: Path, *hosts: str) -> None:
    (allow_dir / _ALLOW_FILE).write_text(
        "# test fixture\n" + "".join(f"{h}\n" for h in hosts), encoding="utf-8"
    )


def test_watcher_applies_a_new_host_with_no_operator_action(imap_egress_stack):
    """The whole point of LUM-758: a Core-style rewrite reaches Squid by itself.

    The observable is the watcher's own reload, not a per-request one. That is a
    scope choice, not a workaround: what LUM-758 owns is "the file gets applied
    without an operator" — no restart, no `squid -k reconfigure`. Whether an
    applied file then permits or refuses a given host is the LUM-625 tests' claim,
    and they assert it above against the peer certificate.
    """
    allow_dir = imap_egress_stack
    env = {**os.environ, "IMAP_EGRESS_ALLOW_DIR": str(allow_dir)}

    # Establish the baseline explicitly rather than inheriting it from whichever
    # tests ran first — these watcher tests mutate module-scoped state, so relying
    # on file order would make them quietly order-dependent.
    _write_allow(allow_dir, _DECLARED_HOST)
    time.sleep(_SETTLE)
    before = _reload_count(env)

    _write_allow(allow_dir, _DECLARED_HOST, _UNDECLARED_HOST)
    time.sleep(_SETTLE)

    assert _reload_count(env) > before, (
        "the watcher should have applied the rewritten allow file with no operator "
        "action — no restart, no `squid -k reconfigure`"
    )


def test_byte_identical_rewrite_does_not_reload(imap_egress_stack):
    """Guards the LUM-758 digest fix.

    Core rewrites this file on every credential change and most rewrites are
    byte-identical. Reloading on those churns Squid's SSL context for nothing — and
    a `*` glob would do exactly that, because Core's temp file lives in the watched
    directory. The canary at the end proves the watcher was alive throughout.
    """
    allow_dir = imap_egress_stack
    env = {**os.environ, "IMAP_EGRESS_ALLOW_DIR": str(allow_dir)}

    _write_allow(allow_dir, _DECLARED_HOST)
    time.sleep(_SETTLE)
    before = _reload_count(env)

    _write_allow(allow_dir, _DECLARED_HOST)  # same bytes, new mtime
    time.sleep(_SETTLE)
    assert _reload_count(env) == before, "a byte-identical rewrite must not reload Squid"

    # CANARY — without this, a dead watcher passes the assertion above.
    _write_allow(allow_dir, _DECLARED_HOST, _UNDECLARED_HOST)
    time.sleep(_SETTLE)
    assert _reload_count(env) > before, (
        "the watcher did not react to a real change either — it was not running, so "
        "the no-reload assertion above proved nothing"
    )
