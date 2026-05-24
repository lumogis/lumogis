# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for paperless-ngx connector + outbound URL policy (LUM-281)."""

from __future__ import annotations

import pytest
from adapters.paperless_source import PaperlessPoller
from connectors.registry import CONNECTORS
from connectors.registry import PAPERLESS
from services.injection_sanitiser import sanitize_attribute_source_token
from services.outbound_http_url import validate_outbound_connector_base_url
from services.paperless_credentials import PaperlessConnection
from services.paperless_credentials import _validate_payload
from services.point_ids import external_document_chunk_point_id


def test_paperless_registered_in_connector_registry():
    assert PAPERLESS in CONNECTORS
    assert "paperless" in CONNECTORS[PAPERLESS].description.lower()


def test_external_document_chunk_point_id_namespaces_users_and_sources():
    a = external_document_chunk_point_id("u1", "s1", "paperless", "42", 0)
    b = external_document_chunk_point_id("u2", "s1", "paperless", "42", 0)
    c = external_document_chunk_point_id("u1", "s2", "paperless", "42", 0)
    assert a != b != c


def test_sanitize_attribute_source_token_roundtrips_paperless_uri():
    uri = "paperless://550e8400-e29b-41d4-a716-446655440000/documents/123"
    assert sanitize_attribute_source_token(uri) == uri


def test_validate_outbound_blocks_metadata_ipv4_with_resolver():
    def _res(_host: str):
        return ["169.254.169.254"]

    with pytest.raises(ValueError, match="169.254"):
        validate_outbound_connector_base_url("http://metadata.example/", resolve_host=_res)


def test_validate_outbound_allows_private_when_flag_true(monkeypatch):
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "true")

    def _res(_host: str):
        return ["10.0.0.5"]

    validate_outbound_connector_base_url("http://svc.internal/", resolve_host=_res)


def test_validate_outbound_blocks_private_when_flag_false(monkeypatch):
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "false")
    monkeypatch.delenv("LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST", raising=False)

    def _res(_host: str):
        return ["10.0.0.5"]

    with pytest.raises(ValueError, match="private"):
        validate_outbound_connector_base_url("http://foo/", resolve_host=_res)


def test_validate_outbound_allowlist_hostname_when_private(monkeypatch):
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "false")
    monkeypatch.setenv("LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST", "paperless")

    def _res(_host: str):
        return ["10.0.0.5"]

    validate_outbound_connector_base_url("http://paperless:8000/", resolve_host=_res)


def test_paperless_payload_validation_roundtrip():
    base_url, token = _validate_payload(
        {"base_url": "http://127.0.0.1:8000", "token": "abc", "extra": 1}
    )
    assert base_url == "http://127.0.0.1:8000"
    assert token == "abc"


def test_paperless_poller_authorization_header_uses_token_scheme():
    conn = PaperlessConnection(base_url="http://paperless:8000", token="tok_test")
    poller = PaperlessPoller(conn)
    assert poller._headers()["Authorization"] == "Token tok_test"
