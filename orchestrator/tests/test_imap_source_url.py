# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-634 + LUM-773 — plaintext IMAP source-url parser and metadata-store read."""

from __future__ import annotations

import pytest
from services.imap_source_url import imap_hostname_from_source_url

from services import imap_source_url as isu


def test_parses_standard_imap_url():
    assert isu.imap_host_port_from_source_url("imap://mail.example.com:993/Work") == (
        "mail.example.com",
        993,
    )


def test_default_port_993():
    assert isu.imap_host_port_from_source_url("imap://mail.example.com/Inbox") == (
        "mail.example.com",
        993,
    )


def test_bad_scheme_returns_none():
    assert isu.imap_host_port_from_source_url("https://mail.example.com:993/x") is None


def test_bad_port_returns_none():
    assert isu.imap_host_port_from_source_url("imap://h:notaport/l") is None
    assert isu.imap_host_port_from_source_url("imap://h:99999/l") is None


def test_ipv6_bracket_value_error_returns_none():
    assert isu.imap_host_port_from_source_url("imap://[::1/label") is None


def test_valid_ipv6_parses():
    assert isu.imap_host_port_from_source_url("imap://[::1]:993/label") == ("::1", 993)


def test_hostname_helper_ignores_bad_port():
    assert (
        imap_hostname_from_source_url("imap://imap.gmail.com:notaport/personal") == "imap.gmail.com"
    )


def test_source_url_for_imap_instance_found(monkeypatch):
    class _Store:
        def fetch_one(self, query, params):
            assert params == ("src-1", "user-a")
            return {"url": "imap://imap.gmail.com:993/Inbox"}

    monkeypatch.setattr(isu.config, "get_metadata_store", lambda: _Store())
    assert isu.source_url_for_imap_instance("user-a", "src-1") == "imap://imap.gmail.com:993/Inbox"


def test_source_url_for_imap_instance_not_found(monkeypatch):
    class _Store:
        def fetch_one(self, query, params):
            return None

    monkeypatch.setattr(isu.config, "get_metadata_store", lambda: _Store())
    assert isu.source_url_for_imap_instance("user-a", "missing") is None


def test_source_url_for_imap_instance_empty_url(monkeypatch):
    class _Store:
        def fetch_one(self, query, params):
            return {"url": "   "}

    monkeypatch.setattr(isu.config, "get_metadata_store", lambda: _Store())
    assert isu.source_url_for_imap_instance("user-a", "src-1") is None


def test_source_url_for_imap_instance_user_id_isolation(monkeypatch):
    """Row returned only when user_id matches — other user's source id → None."""

    class _Store:
        def fetch_one(self, query, params):
            _source_id, user_id = params
            if user_id == "owner":
                return {"url": "imap://mail.example.com:993/x"}
            return None

    monkeypatch.setattr(isu.config, "get_metadata_store", lambda: _Store())
    assert isu.source_url_for_imap_instance("owner", "src-1") == "imap://mail.example.com:993/x"
    assert isu.source_url_for_imap_instance("other", "src-1") is None


def test_source_url_for_imap_instance_propagates_fetch_error(monkeypatch):
    class _Store:
        def fetch_one(self, query, params):
            raise RuntimeError("db down")

    monkeypatch.setattr(isu.config, "get_metadata_store", lambda: _Store())
    with pytest.raises(RuntimeError, match="db down"):
        isu.source_url_for_imap_instance("user-a", "src-1")


# --- LUM-782: IDNA / punycode host normalisation ---------------------------------

_PUNY = "xn--bcher-kva.example"


@pytest.mark.parametrize(
    "host",
    ["bücher.example", "Bücher.Example", _PUNY, "XN--BCHER-KVA.EXAMPLE", "bücher.example."],
)
def test_normalize_idn_forms_converge_on_a_label(host):
    assert isu.normalize_imap_host(host) == _PUNY


def test_normalize_trailing_dot_and_case_ascii():
    assert isu.normalize_imap_host("IMAP.Gmail.com.") == "imap.gmail.com"


def test_normalize_is_idna2008_not_2003():
    # IDNA2003 would fold ß to "ss" (a different host); IDNA2008 keeps it.
    assert isu.normalize_imap_host("faß.de") == "xn--fa-hia.de"


def test_normalize_ip_literals():
    assert isu.normalize_imap_host("192.168.0.1") == "192.168.0.1"
    assert isu.normalize_imap_host("::1") == "::1"


def test_normalize_keeps_existing_non_ldh_ascii_hosts():
    assert isu.normalize_imap_host("my_host.lan") == "my_host.lan"


@pytest.mark.parametrize(
    "host", ["", ".", "a..b", "xn--zz.example", "a" * 64 + ".com", "bücher..example"]
)
def test_normalize_invalid_returns_none(host):
    assert isu.normalize_imap_host(host) is None


def test_parse_normalises_stored_unicode_and_trailing_dot_rows():
    assert isu.imap_host_port_from_source_url("imap://bücher.example:993/x") == (_PUNY, 993)
    assert isu.imap_host_port_from_source_url(f"imap://{_PUNY}./x") == (_PUNY, 993)


def test_parse_invalid_host_returns_none():
    assert isu.imap_host_port_from_source_url("imap://xn--zz.example:993/x") is None
