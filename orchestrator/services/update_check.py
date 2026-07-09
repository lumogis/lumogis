# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only update availability check for admin diagnostics (LUM-187).

Compares the running Core version (``__version__``) against the latest published
GitHub release of the public repo. Fail-soft by design: any error (disabled,
network, parse) returns ``checked=False`` with an ``error`` string and
``update_available=False`` so the admin UI degrades gracefully. There is no
auto-update — the operator triggers ``make update`` explicitly.

Configuration (env):
* ``LUMOGIS_UPDATE_CHECK_ENABLED`` — ``"0"`` disables the network call (default on).
* ``LUMOGIS_UPDATE_REPO`` — ``owner/repo`` to query (default ``lumogis/lumogis``).
* ``LUMOGIS_UPDATE_CHECK_TIMEOUT`` — per-request seconds (default ``4.0``).
* ``LUMOGIS_UPDATE_CHECK_CACHE_TTL_SECONDS`` — in-process TTL for successful
  ``releases/latest`` lookups (default ``3600``; ``0`` disables caching).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from time import monotonic

from __version__ import __version__ as CORE_VERSION
from models.api_v1 import UpdateStatusResponse
from packaging.version import InvalidVersion
from packaging.version import Version

_log = logging.getLogger(__name__)

_DEFAULT_REPO = "lumogis/lumogis"
_DEFAULT_TIMEOUT = 4.0
_DEFAULT_CACHE_TTL = 3600.0


@dataclass(frozen=True)
class _ReleaseCacheEntry:
    payload: dict
    expires_at: float


_release_cache: dict[str, _ReleaseCacheEntry] = {}


def _enabled() -> bool:
    return os.getenv("LUMOGIS_UPDATE_CHECK_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _repo() -> str:
    return os.getenv("LUMOGIS_UPDATE_REPO", _DEFAULT_REPO).strip() or _DEFAULT_REPO


def _timeout() -> float:
    raw = os.getenv("LUMOGIS_UPDATE_CHECK_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT
    except ValueError:
        return _DEFAULT_TIMEOUT


def _cache_ttl() -> float:
    raw = os.getenv("LUMOGIS_UPDATE_CHECK_CACHE_TTL_SECONDS", "").strip()
    try:
        return float(raw) if raw else _DEFAULT_CACHE_TTL
    except ValueError:
        return _DEFAULT_CACHE_TTL


def clear_release_cache_for_tests() -> None:
    """Drop cached release payloads (unit tests only)."""
    _release_cache.clear()


def _normalize_tag(tag: str) -> str:
    """Strip a leading ``v`` so ``v0.3.0`` parses like ``0.3.0``."""
    tag = tag.strip()
    return tag[1:] if tag[:1] in {"v", "V"} else tag


def fetch_latest_release(repo: str, timeout: float) -> dict:
    """Fetch the latest release JSON from GitHub. Isolated for test monkeypatching.

    Successful responses are cached in-process per ``repo`` for
    ``LUMOGIS_UPDATE_CHECK_CACHE_TTL_SECONDS`` (default 1h) so concurrent admin
    probes do not exhaust GitHub's unauthenticated rate limit.

    Returns the parsed JSON dict (expects ``tag_name``; ``html_url`` optional).
    Raises on any HTTP/transport error so the caller can fail-soft.
    """
    ttl = _cache_ttl()
    if ttl > 0:
        entry = _release_cache.get(repo)
        if entry is not None and monotonic() < entry.expires_at:
            return entry.payload

    import httpx

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "lumogis-update-check"}
    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    payload = resp.json()
    if ttl > 0:
        _release_cache[repo] = _ReleaseCacheEntry(payload=payload, expires_at=monotonic() + ttl)
    return payload


def build_update_status_response() -> UpdateStatusResponse:
    """Build the admin update-status payload (fail-soft; never raises)."""
    current = CORE_VERSION

    if not _enabled():
        return UpdateStatusResponse(
            current_version=current,
            checked=False,
            error="update check disabled (LUMOGIS_UPDATE_CHECK_ENABLED=0)",
        )

    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        data = fetch_latest_release(_repo(), _timeout())
    except Exception as exc:  # network, HTTP, JSON — all fail-soft
        _log.info("update_check: release lookup failed (%s)", exc.__class__.__name__)
        return UpdateStatusResponse(
            current_version=current,
            checked=False,
            checked_at=checked_at,
            error=f"release lookup failed: {exc.__class__.__name__}",
        )

    tag = (data.get("tag_name") or "").strip()
    if not tag:
        return UpdateStatusResponse(
            current_version=current,
            checked=False,
            checked_at=checked_at,
            error="latest release has no tag_name",
        )

    release_url = data.get("html_url") or None
    update_available = False
    try:
        update_available = Version(_normalize_tag(tag)) > Version(current)
    except InvalidVersion:
        # Non-PEP-440 tag (or odd current): report the tag but don't claim an update.
        _log.info("update_check: unparseable version (current=%s tag=%s)", current, tag)
        return UpdateStatusResponse(
            current_version=current,
            latest_version=tag,
            update_available=False,
            checked=True,
            checked_at=checked_at,
            release_url=release_url,
            error="could not compare versions (non-PEP440 tag)",
        )

    return UpdateStatusResponse(
        current_version=current,
        latest_version=tag,
        update_available=update_available,
        checked=True,
        checked_at=checked_at,
        release_url=release_url,
    )
