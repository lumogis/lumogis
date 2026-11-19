# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Client for the stack-control sidecar — the only place Core knows its URL and token.

stack-control restarts compose services and reports their status. It has no host
port and accepts a request only with ``X-Lumogis-Restart-Token`` equal to the
current ``RESTART_SECRET``. Before LUM-331 the URL and the token lookup were copied
into four callers (LibreChat sync and restart in ``routes/admin.py``, the stack
status service, and LibreChat config sync at start-up in ``main.py``); this module
replaces all of them. New sidecar calls go through :func:`post` / :func:`get`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_URL = "http://stack-control:9000"
TOKEN_HEADER = "X-Lumogis-Restart-Token"

# Read at call time, not import time: tests point it at a temp file.
PROJECT_ENV_FILE = Path("/project/.env")


def base_url() -> str:
    """``STACK_CONTROL_URL`` (no trailing slash), default ``http://stack-control:9000``."""
    return os.environ.get("STACK_CONTROL_URL", DEFAULT_URL).rstrip("/")


def token() -> str:
    """The current ``RESTART_SECRET``, read from ``/project/.env`` at call time.

    The entrypoint generates the secret on first boot and writes it to
    ``/project/.env``, while this process's own environment still holds the old
    placeholder. Reading the file means we always send the token stack-control
    expects. Falls back to the ``RESTART_SECRET`` environment variable, then ``""``.
    """
    if PROJECT_ENV_FILE.is_file():
        try:
            for line in PROJECT_ENV_FILE.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("RESTART_SECRET="):
                    return stripped[len("RESTART_SECRET=") :].strip()
        except Exception:
            pass
    return os.environ.get("RESTART_SECRET", "")


def _headers() -> dict[str, str]:
    return {TOKEN_HEADER: token()}


def post(path: str, *, json: Any = None, timeout: float) -> httpx.Response:
    """``POST {base_url()}{path}`` with the token header. Raises ``httpx`` errors."""
    return httpx.post(f"{base_url()}{path}", headers=_headers(), json=json, timeout=timeout)


def get(path: str, *, timeout: float) -> httpx.Response:
    """``GET {base_url()}{path}`` with the token header. Raises ``httpx`` errors."""
    with httpx.Client(timeout=timeout) as client:
        return client.get(f"{base_url()}{path}", headers=_headers())
