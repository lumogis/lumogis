# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Caddy must close idle orchestrator connections before uvicorn does (LUM-875).

Caddy pools upstream connections. If uvicorn's keep-alive timeout is shorter
than Caddy's idle timeout, uvicorn can close a connection just as Caddy reuses
it. The request then gets EOF or connection reset, Caddy does not retry a
POST, and the client sees 502. A failed ``POST /api/v1/auth/refresh`` signs the
user out. With uvicorn's default of 5 s this happened in the Playwright lane.

The invariant, read straight from the shipped config files:

* every uvicorn launch in the container (``orchestrator/Dockerfile`` CMD and the
  ``docker-compose.dev.yml`` override) passes an explicit
  ``--timeout-keep-alive``;
* every ``reverse_proxy`` to the orchestrator in ``docker/caddy/Caddyfile``
  sets a ``keepalive`` idle timeout, because Caddy's default is 2 minutes;
* each such Caddy keepalive is strictly shorter than uvicorn's timeout.

The live race is shown by ``scripts/check-caddy-upstream-keepalive.sh``
(``make caddy-keepalive-check``). This test keeps the config from drifting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from tests._compose_helpers import caddy_block_after

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "orchestrator" / "Dockerfile"
DEV_COMPOSE = REPO / "docker-compose.dev.yml"
CADDYFILE = REPO / "docker" / "caddy" / "Caddyfile"

UVICORN_DEFAULT_KEEPALIVE_S = 5.0
CADDY_DEFAULT_KEEPALIVE_S = 120.0

pytestmark = pytest.mark.skipif(
    not CADDYFILE.is_file(),
    reason="repo tree not present (e.g. orchestrator-only test container)",
)


def keepalive_from_argv(argv: list[str]) -> float:
    """uvicorn's effective ``--timeout-keep-alive`` for a CLI argv."""
    for i, arg in enumerate(argv):
        if arg == "--timeout-keep-alive":
            return float(argv[i + 1])
        if arg.startswith("--timeout-keep-alive="):
            return float(arg.split("=", 1)[1])
    return UVICORN_DEFAULT_KEEPALIVE_S


def dockerfile_cmd(text: str) -> list[str]:
    cmds = [line for line in text.splitlines() if line.startswith("CMD ")]
    assert cmds, "orchestrator/Dockerfile has no CMD"
    return json.loads(cmds[-1][len("CMD ") :])


def _duration_s(token: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", token)
    assert m, f"unparsed Caddy duration {token!r}"
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[m.group(2)]
    return float(m.group(1)) * scale


def caddy_orchestrator_keepalives(text: str) -> list[tuple[int, float]]:
    """``(line, idle timeout s)`` for each ``reverse_proxy`` to the orchestrator."""
    # Comments would otherwise match (the snippet's own explanation names the
    # directive), so blank them while keeping offsets and line numbers.
    code = re.sub(r"#[^\n]*", lambda m: " " * len(m.group(0)), text)
    found: list[tuple[int, float]] = []
    for m in re.finditer(r"\breverse_proxy\b[^\n{]*\borchestrator:8000\b", code):
        line = code.count("\n", 0, m.start()) + 1
        block = caddy_block_after(code, m.start())
        ka = re.search(r"\bkeepalive\s+(\S+)", block or "")
        if ka is None:
            found.append((line, CADDY_DEFAULT_KEEPALIVE_S))
        elif ka.group(1) == "off":
            found.append((line, 0.0))
        else:
            found.append((line, _duration_s(ka.group(1))))
    return found


def test_dockerfile_cmd_sets_keepalive() -> None:
    argv = dockerfile_cmd(DOCKERFILE.read_text())
    assert argv[:2] == ["uvicorn", "main:app"]
    assert keepalive_from_argv(argv) > UVICORN_DEFAULT_KEEPALIVE_S, (
        "orchestrator/Dockerfile CMD must pass --timeout-keep-alive; uvicorn's "
        "5 s default lets Caddy reuse closed connections (LUM-875)"
    )


def test_dev_overlay_matches_dockerfile() -> None:
    """``command:`` in the dev overlay replaces the CMD, so it needs the flag too."""
    dev = yaml.safe_load(DEV_COMPOSE.read_text())
    argv = dev["services"]["orchestrator"]["command"]
    assert keepalive_from_argv(argv) == keepalive_from_argv(dockerfile_cmd(DOCKERFILE.read_text()))


def test_every_orchestrator_route_has_caddy_keepalive_below_uvicorn() -> None:
    uvicorn_s = keepalive_from_argv(dockerfile_cmd(DOCKERFILE.read_text()))
    proxies = caddy_orchestrator_keepalives(CADDYFILE.read_text())
    assert proxies, "no reverse_proxy to orchestrator:8000 found in the Caddyfile"
    too_long = [(line, s) for line, s in proxies if s >= uvicorn_s]
    assert not too_long, (
        f"Caddyfile reverse_proxy blocks whose upstream keepalive is not below "
        f"uvicorn's --timeout-keep-alive ({uvicorn_s:g} s): {too_long}. Use "
        f"`import to_orchestrator` or set `transport http {{ keepalive … }}`."
    )


# --- the parsers themselves, so a silent parse miss cannot pass the checks ---


def test_keepalive_from_argv() -> None:
    assert keepalive_from_argv(["uvicorn", "main:app"]) == 5.0
    assert keepalive_from_argv(["uvicorn", "--timeout-keep-alive", "75"]) == 75.0
    assert keepalive_from_argv(["uvicorn", "--timeout-keep-alive=30"]) == 30.0


def test_caddy_parser_flags_bare_and_long_proxies() -> None:
    text = """
(up) {
\treverse_proxy orchestrator:8000 {
\t\ttransport http {
\t\t\tkeepalive 60s
\t\t}
\t}
}
:80 {
\t# reverse_proxy orchestrator:8000 in a comment is ignored
\thandle /a { import up }
\thandle /b {
\t\treverse_proxy orchestrator:8000
\t}
\thandle /c {
\t\treverse_proxy orchestrator:8000 {
\t\t\tflush_interval -1
\t\t\ttransport http {
\t\t\t\tkeepalive 2m
\t\t\t}
\t\t}
\t}
\thandle { reverse_proxy lumogis-web:80 }
}
"""
    assert [s for _, s in caddy_orchestrator_keepalives(text)] == [60.0, 120.0, 120.0]
