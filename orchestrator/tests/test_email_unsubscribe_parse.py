# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``parse_list_unsubscribe`` — the security boundary of LUM-697.

The header is written by whoever sent the message, so this parser is the only
thing standing between a hostile sender and (a) a URI we render as a clickable
link and (b) an address we send mail to. Two of these tests pin published CVEs.
"""

from __future__ import annotations

import pytest
from services.email_unsubscribe import parse_list_unsubscribe


# --------------------------------------------------------------------------- #
# happy paths
# --------------------------------------------------------------------------- #
def test_parses_mailto_and_https_from_one_header():
    out = parse_list_unsubscribe("<mailto:u@x.com>, <https://x.com/u?t=1>")
    assert out.mailto_addr == "u@x.com"
    assert out.https_uri == "https://x.com/u?t=1"


def test_parses_mailto_subject_parameter():
    out = parse_list_unsubscribe("<mailto:u@x.com?subject=unsub%20me>")
    assert out.mailto_addr == "u@x.com"
    assert out.mailto_subject == "unsub me"


def test_comma_inside_angle_brackets_is_not_a_separator():
    # Query strings routinely contain commas; a naive split(",") shreds the URI.
    out = parse_list_unsubscribe("<https://x.com/u?ids=a,b,c>")
    assert out.https_uri == "https://x.com/u?ids=a,b,c"


# --------------------------------------------------------------------------- #
# scheme allowlist — the CVE regressions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "uri",
    [
        "<javascript:alert(1)>",  # Horde stored XSS — CVE-2025-68673
        "<data:text/html,<script>alert(1)</script>>",
        "<file:///etc/passwd>",
        "<ftp://x.com/u>",
        "<http://x.com/u>",  # RFC 8058 requires https
        "<vbscript:msgbox>",
    ],
)
def test_rejects_every_scheme_outside_the_allowlist(uri):
    out = parse_list_unsubscribe(uri)
    assert out.is_empty(), f"{uri!r} should have been dropped"


def test_keeps_the_valid_uri_when_a_sibling_is_malicious():
    # Dropping the whole header because one member is hostile would let an
    # attacker disable unsubscribe for a sender by appending junk.
    out = parse_list_unsubscribe("<javascript:alert(1)>, <mailto:u@x.com>")
    assert out.mailto_addr == "u@x.com"
    assert out.https_uri == ""


def test_rejects_https_uri_with_userinfo():
    # https://evil@real-bank.example/ displays as real-bank.example in some UIs
    # while resolving to evil. We render this value as a clickable link.
    out = parse_list_unsubscribe("<https://evil@real-bank.example/u>")
    assert out.https_uri == ""


def test_rejects_https_uri_with_embedded_credentials():
    out = parse_list_unsubscribe("<https://user:pw@x.com/u>")
    assert out.https_uri == ""


# --------------------------------------------------------------------------- #
# fan-out prevention
# --------------------------------------------------------------------------- #
def test_only_the_first_mailto_address_is_taken():
    # A crafted multi-address mailto must not become a send to three recipients
    # the user never reviewed.
    out = parse_list_unsubscribe("<mailto:a@x.com,b@y.com,c@z.com>")
    assert out.mailto_addr == "a@x.com"


def test_malformed_mailto_address_is_dropped():
    assert parse_list_unsubscribe("<mailto:not-an-address>").mailto_addr == ""


def test_crlf_in_subject_is_stripped_at_parse():
    # Header injection at send time; dropping it here means it never reaches the DB.
    out = parse_list_unsubscribe("<mailto:u@x.com?subject=a%0D%0ABcc:evil@x.com>")
    assert out.mailto_addr == "u@x.com"
    assert out.mailto_subject == ""


# --------------------------------------------------------------------------- #
# bounds and one-click
# --------------------------------------------------------------------------- #
def test_over_long_uri_is_dropped():
    assert parse_list_unsubscribe("<https://x.com/" + "a" * 3000 + ">").https_uri == ""


def test_one_click_true_only_on_the_exact_rfc_8058_value():
    assert parse_list_unsubscribe("<mailto:u@x.com>", "List-Unsubscribe=One-Click").one_click
    assert not parse_list_unsubscribe("<mailto:u@x.com>", "One-Click").one_click
    assert not parse_list_unsubscribe("<mailto:u@x.com>", "").one_click


def test_one_click_is_case_and_whitespace_insensitive():
    out = parse_list_unsubscribe("<mailto:u@x.com>", "  list-unsubscribe = ONE-CLICK  ")
    assert out.one_click is True


@pytest.mark.parametrize("raw", ["", "<>", "))((", "not a uri at all", "<mailto:>", ","])
def test_empty_and_garbage_headers_never_raise(raw):
    assert parse_list_unsubscribe(raw).is_empty()
