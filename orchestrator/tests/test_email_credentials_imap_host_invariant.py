# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-773 — validate_imap_instance_payload_against_source unit tests."""

from __future__ import annotations

import pytest
from services.email_credentials import ImapHostMismatchError
from services.email_credentials import ImapSourceNotFoundError
from services.email_credentials import ImapSourceUrlInvalidError
from services.email_credentials import validate_imap_instance_payload_against_source


class _SourceStore:
    def __init__(self, rows: dict[tuple[str, str], str]) -> None:
        self._rows = rows

    def fetch_one(self, query, params=None):
        p = params or ()
        source_id, user_id = p
        url = self._rows.get((source_id, user_id))
        if url is None:
            return None
        return {"url": url}


def test_validate_accepts_matching_host_port():
    import config as _config

    store = _SourceStore({("src-1", "user-a"): "imap://imap.gmail.com:993/personal"})
    _config._instances["metadata_store"] = store
    payload = {
        "host": "imap.gmail.com",
        "port": 993,
        "username": "u",
        "password": "p",
    }
    validate_imap_instance_payload_against_source("user-a", "src-1", payload)
    _config._instances.pop("metadata_store", None)


def test_validate_rejects_host_mismatch():
    import config as _config

    store = _SourceStore({("src-1", "user-a"): "imap://imap.gmail.com:993/personal"})
    _config._instances["metadata_store"] = store
    with pytest.raises(ImapHostMismatchError):
        validate_imap_instance_payload_against_source(
            "user-a",
            "src-1",
            {"host": "mail.other.com", "port": 993, "username": "u", "password": "p"},
        )
    _config._instances.pop("metadata_store", None)


def test_validate_rejects_port_mismatch():
    import config as _config

    store = _SourceStore({("src-1", "user-a"): "imap://imap.gmail.com:993/personal"})
    _config._instances["metadata_store"] = store
    with pytest.raises(ImapHostMismatchError):
        validate_imap_instance_payload_against_source(
            "user-a",
            "src-1",
            {"host": "imap.gmail.com", "port": 143, "username": "u", "password": "p"},
        )
    _config._instances.pop("metadata_store", None)


def test_validate_normalizes_host_case():
    import config as _config

    store = _SourceStore({("src-1", "user-a"): "imap://imap.gmail.com:993/personal"})
    _config._instances["metadata_store"] = store
    validate_imap_instance_payload_against_source(
        "user-a",
        "src-1",
        {"host": "IMAP.Gmail.com", "port": 993, "username": "u", "password": "p"},
    )
    _config._instances.pop("metadata_store", None)


def test_validate_raises_source_not_found_when_instance_unknown():
    import config as _config

    _config._instances["metadata_store"] = _SourceStore({})
    with pytest.raises(ImapSourceNotFoundError):
        validate_imap_instance_payload_against_source(
            "user-a",
            "missing",
            {"host": "h", "port": 993, "username": "u", "password": "p"},
        )
    _config._instances.pop("metadata_store", None)


def test_validate_raises_url_invalid_on_malformed_source_url():
    import config as _config

    store = _SourceStore({("src-1", "user-a"): "imap://host:notaport/personal"})
    _config._instances["metadata_store"] = store
    with pytest.raises(ImapSourceUrlInvalidError):
        validate_imap_instance_payload_against_source(
            "user-a",
            "src-1",
            {"host": "host", "port": 993, "username": "u", "password": "p"},
        )
    _config._instances.pop("metadata_store", None)


def test_validate_rejects_whitespace_host_via_value_error():
    import config as _config

    store = _SourceStore({("src-1", "user-a"): "imap://imap.gmail.com:993/personal"})
    _config._instances["metadata_store"] = store
    with pytest.raises(ValueError, match="whitespace"):
        validate_imap_instance_payload_against_source(
            "user-a",
            "src-1",
            {"host": " imap.gmail.com", "port": 993, "username": "u", "password": "p"},
        )
    _config._instances.pop("metadata_store", None)


def test_validate_rejects_string_port_via_value_error():
    import config as _config

    store = _SourceStore({("src-1", "user-a"): "imap://imap.gmail.com:993/personal"})
    _config._instances["metadata_store"] = store
    with pytest.raises(ValueError, match="port"):
        validate_imap_instance_payload_against_source(
            "user-a",
            "src-1",
            {"host": "imap.gmail.com", "port": "993", "username": "u", "password": "p"},
        )
    _config._instances.pop("metadata_store", None)


# --- LUM-782: IDNA / punycode host pairs ------------------------------------------


def _validate(stored_url: str, host: str) -> None:
    import config as _config

    _config._instances["metadata_store"] = _SourceStore({("src-1", "user-a"): stored_url})
    try:
        validate_imap_instance_payload_against_source(
            "user-a", "src-1", {"host": host, "port": 993, "username": "u", "password": "p"}
        )
    finally:
        _config._instances.pop("metadata_store", None)


@pytest.mark.parametrize(
    "stored,got",
    [
        ("imap://bücher.example:993/x", "xn--bcher-kva.example"),
        ("imap://xn--bcher-kva.example:993/x", "bücher.example"),
        ("imap://xn--bcher-kva.example:993/x", "BÜCHER.Example"),
        ("imap://xn--bcher-kva.example:993/x", "xn--bcher-kva.example."),
        ("imap://mail.example.com:993/x", "Mail.Example.COM."),
    ],
)
def test_validate_accepts_equivalent_idn_forms(stored, got):
    _validate(stored, got)


@pytest.mark.parametrize("got", ["xn--zz.example", "a..b", "bücher..example", "büchér.example"])
def test_validate_invalid_or_different_host_fails_closed(got):
    with pytest.raises(ImapHostMismatchError):
        _validate("imap://xn--bcher-kva.example:993/x", got)
