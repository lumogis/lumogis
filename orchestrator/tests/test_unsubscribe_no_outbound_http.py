# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The ADR's load-bearing guarantee, enforced by execution (LUM-697).

*"Lumogis will not fetch a URI extracted from message content server-side."*

A `List-Unsubscribe` URI is written by whoever sent the message. Fetching it
server-side is a request to an attacker-chosen host, from inside the network
boundary, with no DKIM signature to distinguish a genuine RFC 8058 header from a
forged one — and the LUM-571 egress ceiling bakes its allowlist at boot, so a
runtime-discovered host is not expressible in it anyway. Nextcloud Mail shipped
this and took a blind-SSRF advisory (GHSA-4pp4-m8ph-2999 / CVE-2023-48307).

A comment saying "do not add an HTTP client here" is not a control. This is.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules that must never reach the network. The route module is included
# deliberately: it *returns* https_uri to the browser, which is exactly the kind
# of place someone later "helpfully" adds a server-side prefetch.
_MODULES = (
    "services/email_unsubscribe.py",
    "services/email_sender_rules.py",
    "routes/api_v1/email_unsubscribe.py",
    # LUM-711: handles historical `List-Unsubscribe` URIs in bulk — the shape
    # that most invites a helpful "while we're here, just fetch them all".
    "services/email_unsubscribe_backfill.py",
    # LUM-710: runs off the request thread, where a slow outbound fetch would
    # not even be noticed as latency. That makes it *more* attractive as a place
    # to add one, not less.
    "services/batch_handlers/email_unsubscribe_batch.py",
)

_FORBIDDEN_IMPORTS = {
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
    "aiohttp",
    "http.client",
    "socket",
    "ftplib",
    "telnetlib",
}
_FORBIDDEN_CALLS = {"urlopen", "urlretrieve", "get", "post", "put", "patch", "delete"}
_FORBIDDEN_CALL_ROOTS = {"httpx", "requests", "aiohttp", "urllib"}


@pytest.mark.parametrize("relpath", _MODULES)
def test_no_outbound_http_imports(relpath):
    tree = ast.parse((_ROOT / relpath).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                assert a.name not in _FORBIDDEN_IMPORTS and root not in _FORBIDDEN_CALL_ROOTS, (
                    f"{relpath} imports {a.name!r} — this module must never reach the "
                    "network (cursor/adrs/email-unsubscribe.md)"
                )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            # `from urllib.parse import ...` is fine — parsing is not fetching.
            if mod.startswith("urllib.parse"):
                continue
            assert mod not in _FORBIDDEN_IMPORTS and root not in _FORBIDDEN_CALL_ROOTS, (
                f"{relpath} imports from {mod!r} — this module must never reach the "
                "network (cursor/adrs/email-unsubscribe.md)"
            )


@pytest.mark.parametrize("relpath", _MODULES)
def test_no_outbound_http_calls(relpath):
    tree = ast.parse((_ROOT / relpath).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            assert not (fn.value.id in _FORBIDDEN_CALL_ROOTS and fn.attr in _FORBIDDEN_CALLS), (
                f"{relpath} calls {fn.value.id}.{fn.attr}() — no server-side fetch"
            )
        if isinstance(fn, ast.Name):
            assert fn.id not in {"urlopen", "urlretrieve"}, (
                f"{relpath} calls {fn.id}() — no server-side fetch"
            )


def test_https_uri_is_stored_and_returned_but_never_fetched():
    """Belt and braces: the value flows through to the API response, and the
    modules that carry it contain no fetch primitive (asserted above)."""
    from services.email_unsubscribe import parse_list_unsubscribe

    out = parse_list_unsubscribe("<https://sender.example/unsub?tok=abc>")
    assert out.https_uri == "https://sender.example/unsub?tok=abc"
