#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-625 — guard: the email-IMAP Layer-2 Squid wiring must not be silently dropped.

The cooperative-audit layer only fires if ``docker/egress-proxy/squid.conf`` keeps
the email-IMAP ``CONNECT`` ACLs and the committed allow file exists. This guard
asserts those invariants so a refactor of the egress proxy config can't quietly
remove IMAP containment. Runs offline (no Docker) — cheap CI gate alongside the
live ``make egress-imap-test``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SQUID = _ROOT / "docker" / "egress-proxy" / "squid.conf"
_ALLOW = _ROOT / "docker" / "egress-proxy" / "allow" / "lumogis-email-imap.txt"

# Substrings that must all be present in squid.conf for the CONNECT-tunnel audit
# to work: the SNI ACL bound to the allow file, the IMAPS port ACL, the splice
# rule, and the scoped allow rule.
_REQUIRED_SQUID = (
    'acl email_imap_sni  ssl::server_name "/etc/squid/allow/lumogis-email-imap.txt"',
    "acl imaps_port      port 993",
    # The source scope is load-bearing: without it, contained community
    # capabilities on the shared proxy could use this rule (LUM-625 P0).
    "acl core_egress_src src ",
    "ssl_bump splice email_imap_sni",
    "http_access allow core_egress_src CONNECT_method imaps_port email_imap_sni",
)


def check() -> list[str]:
    problems: list[str] = []
    if not _ALLOW.is_file():
        problems.append(f"missing committed allow file: {_ALLOW.relative_to(_ROOT)}")
    if not _SQUID.is_file():
        problems.append(f"missing squid.conf: {_SQUID.relative_to(_ROOT)}")
        return problems
    text = _SQUID.read_text(encoding="utf-8")
    for needle in _REQUIRED_SQUID:
        if needle not in text:
            problems.append(f"squid.conf missing required LUM-625 line: {needle!r}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("check_email_imap_egress_wiring: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("check_email_imap_egress_wiring: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
