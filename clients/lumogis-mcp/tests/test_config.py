# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""Tests for lumogis_mcp.config."""

from __future__ import annotations

import os

import pytest

from lumogis_mcp.config import ConfigError
from lumogis_mcp.config import load_config
from lumogis_mcp.config import normalize_mcp_url


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("LUMOGIS_MCP_TOKEN", "lmcp_testtoken")
    monkeypatch.delenv("LUMOGIS_MCP_URL", raising=False)
    cfg = load_config()
    assert cfg.url == "http://127.0.0.1:8000/mcp/"
    assert cfg.token == "lmcp_testtoken"


def test_config_rejects_remote_url(monkeypatch):
    monkeypatch.setenv("LUMOGIS_MCP_URL", "https://evil.example/mcp/")
    with pytest.raises(ConfigError, match="loopback"):
        load_config()


def test_config_allows_missing_token(monkeypatch):
    monkeypatch.delenv("LUMOGIS_MCP_TOKEN", raising=False)
    monkeypatch.delenv("LUMOGIS_MCP_URL", raising=False)
    cfg = load_config()
    assert cfg.token is None


def test_trailing_slash_url_normalized():
    assert normalize_mcp_url("http://127.0.0.1:8000/mcp") == "http://127.0.0.1:8000/mcp/"


def test_config_rejects_private_range(monkeypatch):
    monkeypatch.setenv("LUMOGIS_MCP_URL", "http://192.168.1.1:8000/mcp/")
    with pytest.raises(ConfigError, match="loopback"):
        load_config()


def test_config_allows_localhost(monkeypatch):
    monkeypatch.setenv("LUMOGIS_MCP_URL", "http://localhost:8000/mcp/")
    monkeypatch.setenv("LUMOGIS_MCP_TOKEN", "t")
    cfg = load_config()
    assert cfg.url == "http://localhost:8000/mcp/"
