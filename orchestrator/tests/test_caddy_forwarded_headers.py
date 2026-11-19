# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Caddy's forwarded-header defaults must reach the orchestrator untouched (LUM-981).

With ``LUMOGIS_PUBLIC_ORIGIN`` unset, Core derives the browser's origin from the
request: the ``Host`` header (port kept) and ``X-Forwarded-Proto``. Caddy v2's
``reverse_proxy`` already does the right thing by default:

* it passes the client's ``Host`` through **with its port**;
* it sets ``X-Forwarded-Proto`` to its own scheme, unless the request comes from
  a ``trusted_proxies`` upstream, whose value it keeps.

An explicit ``header_up Host {host}`` would strip the port, and an explicit
``header_up X-Forwarded-Proto {scheme}`` (or ``-X-Forwarded-Proto``) would
override a trusted upstream's value. So no ``reverse_proxy orchestrator:8000``
block may contain them. This is a static guard on the shipped config.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests._compose_helpers import caddy_block_after

CADDYFILE = Path(__file__).resolve().parents[2] / "docker" / "caddy" / "Caddyfile"

pytestmark = pytest.mark.skipif(
    not CADDYFILE.is_file(),
    reason="repo tree not present (e.g. orchestrator-only test container)",
)

_FORBIDDEN = re.compile(r"^\s*header_up\s+-?(host|x-forwarded-proto)\b", re.I | re.M)


def _orchestrator_proxy_blocks(text: str) -> list[str]:
    bodies = []
    for m in re.finditer(r"^\s*reverse_proxy\s+orchestrator:8000\b", text, re.M):
        bodies.append(caddy_block_after(text, m.start()))
    return bodies


def test_there_are_orchestrator_proxy_blocks_to_check():
    # Guard against the regex silently matching nothing.
    assert len(_orchestrator_proxy_blocks(CADDYFILE.read_text())) >= 2


def test_no_orchestrator_proxy_block_overrides_host_or_forwarded_proto():
    for body in _orchestrator_proxy_blocks(CADDYFILE.read_text()):
        found = _FORBIDDEN.findall(body)
        assert not found, f"reverse_proxy orchestrator:8000 overrides {found}: {body!r}"


def test_the_guard_would_catch_each_forbidden_line():
    for line in (
        "header_up Host {host}",
        "header_up X-Forwarded-Proto {scheme}",
        "header_up -X-Forwarded-Proto",
    ):
        text = f"reverse_proxy orchestrator:8000 {{\n\t\t{line}\n\t}}\n"
        (body,) = _orchestrator_proxy_blocks(text)
        assert _FORBIDDEN.search(body), line
