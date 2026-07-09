# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""Environment-driven configuration for the Lumogis MCP stdio bridge."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlparse


class ConfigError(Exception):
    """Invalid bridge configuration (URL, host, or env)."""


_DEFAULT_URL = "http://127.0.0.1:8000/mcp/"


@dataclass(frozen=True)
class BridgeConfig:
    """Resolved bridge settings."""

    url: str
    token: str | None


def normalize_mcp_url(raw: str) -> str:
    """Parse and canonicalize the upstream MCP URL (trailing ``/mcp/`` slash)."""
    parsed = urlparse(raw.strip())
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(
            f"LUMOGIS_MCP_URL must use http or https (got {parsed.scheme!r}); "
            f"expected loopback e.g. {_DEFAULT_URL}"
        )
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = f"{path}/"
    if not path.endswith("/mcp/") and path.rstrip("/").endswith("/mcp"):
        path = f"{path.rstrip('/')}/"
    rebuilt = f"{parsed.scheme}://{parsed.netloc}{path}"
    if parsed.query:
        rebuilt = f"{rebuilt}?{parsed.query}"
    return rebuilt


def _hostname(parsed) -> str:
    host = parsed.hostname
    if not host:
        raise ConfigError(f"LUMOGIS_MCP_URL missing hostname: {parsed.geturl()!r}")
    return host.lower().strip("[]")


def _require_loopback_url(url: str) -> None:
    parsed = urlparse(url)
    host = _hostname(parsed)
    if host == "localhost":
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ConfigError(
            f"LUMOGIS_MCP_URL must point at loopback (127.0.0.1 / localhost / ::1); "
            f"refusing {host!r} — Persona A local bridge only"
        )
    if ip.is_loopback:
        return
    if ip.is_private or ip.is_link_local or ip.is_reserved or str(ip) == "0.0.0.0":
        raise ConfigError(
            f"LUMOGIS_MCP_URL must point at loopback (127.0.0.1 / localhost / ::1); "
            f"refusing {host!r} — Persona A local bridge only"
        )
    raise ConfigError(
        f"LUMOGIS_MCP_URL must point at loopback; refusing {host!r}"
    )


def load_config() -> BridgeConfig:
    """Load bridge config from ``LUMOGIS_MCP_URL`` and ``LUMOGIS_MCP_TOKEN``."""
    raw_url = os.environ.get("LUMOGIS_MCP_URL", _DEFAULT_URL)
    url = normalize_mcp_url(raw_url)
    _require_loopback_url(url)

    raw_token = os.environ.get("LUMOGIS_MCP_TOKEN", "")
    token = raw_token.strip() or None

    return BridgeConfig(url=url, token=token)
